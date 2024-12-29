#!/usr/bin/env python3
# vim:set nolist ts=2 sw=2 sts=2 et tw=0:

from __future__ import print_function

import collections
try:
  from absl import flags
except ImportError:
  import warnings
  warnings.warn('Using the deprecated gflags module. Please install absl-py', DeprecationWarning)
  import gflags as flags
import itertools
import logging
import os
import subprocess
import sys
import tempfile

flags.DEFINE_string('decode_locale',
    None,
    'The locale we should run the archive extraction program with. For example '
    'ja_JP.shift-jis.')
flags.DEFINE_string('decode_charset', None, '')
flags.DEFINE_bool('munge_slashes', False, '')
flags.DEFINE_string('override_extension', None, '')
flags.DEFINE_bool('use_7z_for_zip', True,
    'Should we use 7z or unzip to deal with zip archives?')
flags.DEFINE_string('tmp_dir_override', None, '')
flags.DEFINE_bool('extractor_output', False,
    'Should we dump the output from the binary to the screen?')
flags.DEFINE_bool('pretend_case_insensitive', True,
    'Should we recursively walk the output of the archive and merge '
    'directories that only differ by case.')

FLAGS = flags.FLAGS


class UnknownArchiveError(Exception):
  pass


class MergeFailureError(Exception):
  pass


class Extractor(object):
  def __init__(self, tempdir, locale_override):
    self.tempdir = tempdir
    self.locale_override = locale_override
    self._extractors = self.create_extractors_mapping()

  def create_extractors_mapping(self):
    ret = {}
    for ext in {'.cbr', '.rar', '.rar_'}:
      ret[ext] = self._extract_rar
    for ext in {'.cbz', '.egg', '.jar', '.par', '.zip', '.apk', '.xapk', '.crx'}:
      if FLAGS.use_7z_for_zip:
        ret[ext] = self._extract_7z
      else:
        ret[ext] = self._extract_zip
    for ext in {'.7z', '.img', '.iso', '.dmg'}:
      ret[ext] = self._extract_7z
    for ext in {'.bz2', '.gz2', '.tgz', '.tbz', '.tar', '.gz', '.xz', '.zst'}:
      ret[ext] = self._extract_tar
    for ext in {'.deb', }:
      ret[ext] = self._extract_ar
    return ret

  def _get_password(self, container_filename):
    dirname = os.path.dirname(container_filename)
    filenames_to_test = [
        '{}.password'.format(container_filename),
        os.path.join(dirname, 'passwd.txt'),
    ]
    for filename in filenames_to_test:
      if os.path.exists(filename):
        with open(filename) as f:
          return f.read().strip()

  def extract_archive(self, filename):
    """Attempt to extract the archive into the given temp dir.

    Args:
      filename: The archive to extract. This must be a full path (as the 
        extractor will not be running in our cwd.

    Raises:
      UnknownArchiveError: If we don't know how to extract the given extension.
    """
    _, filename_extension = os.path.splitext(filename)
    if FLAGS.override_extension:
      filename_extension = FLAGS.override_extension
    filename_extension = filename_extension.lower()

    extractor_func = self._extractors.get(filename_extension)
    if extractor_func is None:
      raise UnknownArchiveError(
          'Attempting to extract unknown archive extension: {}'.format(
              filename_extension))
    return extractor_func(filename)

  def _run_extractor(self, cmd):
    env = dict(os.environ)
    if self.locale_override:
      env['LC_ALL'] = self.locale_override
    with open('/dev/null', 'r+') as dev_null:
      extractor = subprocess.Popen(
          cmd,
          env=env,
          cwd=self.tempdir,
          stdin=dev_null,
          stderr=subprocess.STDOUT,
          stdout=None if FLAGS.extractor_output else dev_null,
      )
    return extractor.wait()

  # Extractors for different container types.

  def _extract_rar(self, filename):
    password = self._get_password(filename)
    cmdline = ['unrar', 'x']
    cmdline += ['-or']  # Rename files with the same name.
    if password is None:
      cmdline.append('-p-')  # Do not query password
    else:
      cmdline.append('-p{}'.format(password))
    cmdline += ['--', filename]
    return self._run_extractor(cmdline)

  def _extract_zip(self, filename):
    password = self._get_password(filename)
    cmdline = ['unzip']
    if password is not None:
      cmdline.append('-p{}'.format(password))
    cmdline += ['--', filename]
    return self._run_extractor(cmdline)
  
  def _extract_7z(self, filename):
    password = self._get_password(filename)
    cmdline = ['7z', 'x', '-y']
    if password is not None:
      cmdline.append('-p{}'.format(password))
    cmdline += ['--', filename]
    return self._run_extractor(cmdline)

  def _extract_tar(self, filename):
    cmdline = ['tar', 'xvvf']
    cmdline.append(filename)
    return self._run_extractor(cmdline)

  def _extract_ar(self, filename):
    cmdline = ['ar', 'x']
    cmdline += ['--', filename]
    return self._run_extractor(cmdline)


def _listdirs(dirname):
  for entry in os.listdir(dirname):
    yield entry, os.path.isdir(os.path.join(dirname, entry))


def _merge_files_in_directories(remote_dir, local_dir):
  assert remote_dir != local_dir, (
      'Merge files called with both directories equal: %r' % remote_dir)
  logger = logging.getLogger('MergeEntriesInDirectories')
  logger.debug('Merging "%s" and "%s"', remote_dir, local_dir)

  local_dir_entries = {entry: is_dir for entry,is_dir in _listdirs(local_dir)}
  for entry, is_dir in _listdirs(remote_dir):
    if entry in local_dir_entries:
      if not is_dir and not local_dir_entries[entry]:
        # TODO: Verify that entry is the same file in both.
        logger.info('Skipping "%s" as it exists in both directories', entry)
        os.unlink(os.path.join(remote_dir, entry))
        continue
      if not is_dir or not local_dir_entries[entry]:
        # Trying to merge a directory and a file... just bail
        raise MergeFailureError(
            'Failed to merge the directories "%s" and "%s"' % (
              os.path.join(local_dir, entry),
              os.path.join(remote_dir, entry),
            )
        )

      _merge_files_in_directories(
        os.path.join(remote_dir, entry),
        os.path.join(local_dir, entry),
      )
    else:
      os.rename(
          os.path.join(remote_dir, entry),
          s.path.join(local_dir, entry),
      )
  os.rmdir(remote_dir)


def _merge_directories_ignore_case(base_dir):
  logger = logging.getLogger('MergeDirectories')
  dirs = [
      entry for entry in os.listdir(base_dir)
      if os.path.isdir(os.path.join(base_dir, entry))]

  # Merge things that differ by case.
  dir_map = collections.defaultdict(set)
  for dir_ in dirs:
    dir_map[dir_.lower()].add(dir_)

  for k, dirs in dir_map.items():
    if len(dirs) <= 1:
      continue

    # Pick a random dir to be come our base (preferable one that is not all
    # lowercase).
    local_dir = (dirs - {k}).pop()

    dirs.remove(local_dir)
    for remote_dir in dirs:
      _merge_files_in_directories(
          os.path.join(base_dir, remote_dir),
          os.path.join(base_dir, local_dir),
      )
    dir_map[k] = {local_dir}

  for dir_ in itertools.chain.from_iterable(dir_map.values()):
    _merge_directories_ignore_case(
        os.path.join(base_dir, dir_))


def _get_minimal_common_directory(base_dir):
  logger = logging.getLogger('MinimalCommonDir')
  last_dir = base_dir
  for dirpath, dirnames, filenames in os.walk(base_dir):
    last_dir = dirpath
    logger.info('Got, dirpath=%r, dirnames=%r, filenames=%r',
        dirpath, dirnames, filenames)
    if filenames:
      return last_dir
    if len(dirnames) != 1:
      return last_dir
  return last_dir


def main(filenames, tempdir):
  # Get the fully qualified path name for the input file
  filenames = [
      os.path.join(os.getcwd(), filename) for filename in filenames]

  extractor = Extractor(tempdir, locale_override=FLAGS.decode_locale)
  for filename in filenames:
    extractor.extract_archive(filename)

  if FLAGS.decode_charset:
    subprocess.Popen([
      'convmv',
      '-f', FLAGS.decode_charset,
      '-t', 'utf8',
      '--notest',
      '-r', '.'],
      cwd=tempdir,
    ).wait()
  
  if FLAGS.munge_slashes:
    munge_directories(tempdir)

  if FLAGS.pretend_case_insensitive:
    _merge_directories_ignore_case(tempdir)

  subprocess.Popen(
    ['/bin/bash', '-il'],
    # Look to see how many directories are in our output. If there is only
    # one, run the shell there instead.
    cwd=_get_minimal_common_directory(tempdir),
  ).wait()


def munge_directories(tmp_dir):
  for dirpath, dirnames, filenames in os.walk(tmp_dir):
    for dirname in dirnames:
      if '/' in dirname:
        logging.info('move %s to %s', dirname, dirname.replace('\\', '/'))


def removedirs(basedir):
  for dirpath, dirnames, filenames in os.walk(basedir, topdown=False):
    for dirname in dirnames:
      os.rmdir(os.path.join(dirpath, dirname))

    for filename in filenames:
      os.unlink(os.path.join(dirpath, filename))
  os.rmdir(basedir)


def _get_tmpdir():
  base_dir = FLAGS.tmp_dir_override
  if base_dir is not None:
    base_dir = os.path.abspath(base_dir)
  return tempfile.mkdtemp(dir=base_dir)


def _tmpdir_wrapper(argv):
  tempdir = tempfile.mkdtemp(dir=FLAGS.tmp_dir_override)
  try:
    logging.info('Using temp: %s', tempdir)
    return main(filenames=argv[1:], tempdir=tempdir)
  finally:
    # Perform cleanup
    removedirs(tempdir)

def _parse_gflags():
  try:
    argv = FLAGS(sys.argv)
  except gflags.FlagsError as e:
    print('%s\nUsage: %s ARGS\n%s' % (e, sys.argv[0], FLAGS))
    sys.exit(1)
  return _tmpdir_wrapper(argv)

if __name__ == "__main__":
  logging.basicConfig(level=logging.INFO)
  try:
    from absl import app
  except ImportError:
    _parse_gflags()
  else:
    app.run(main=_tmpdir_wrapper)
