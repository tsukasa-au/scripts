#!/usr/bin/env python

from absl import app
from absl import flags
from absl import logging
from typing import List
import enum
import bs4
import dataclasses
import jinja2
import sys


flags.DEFINE_string('input_filename', '/dev/stdin', 'The name of the document to parse')
flags.DEFINE_bool('pretty_print', True, 'The name of the document to parse')
FLAGS = flags.FLAGS


class Alignment(enum.Enum):
  LEFT = enum.auto()
  CENTER = enum.auto()
  RIGHT = enum.auto()


@dataclasses.dataclass
class Table:
  column_headers: List[str]
  rows: List[List[str]]
  max_col_lengths: List[int]
  alignments: List[Alignment]


def parse_doc(soup):
  column_headers = []
  rows = []
  # The internet is horrid... We iterate over all tables, and just stitch them into on.
  for table in soup.find_all('table'):
    if (thead := table.find('thead', recursive=False)) is not None:
      for tr in thead.find_all('tr', recursive=False):
        for elem in tr.find_all(['td', 'th'], recursive=False):
          column_headers.append(' '.join(elem.stripped_strings))
    for tbody in table.find_all('tbody', recursive=False):
      for tr in tbody.find_all('tr', recursive=False):
        values = []
        for td in tr.find_all('td', recursive=False):
          values.append(' '.join(td.stripped_strings))
          if 'colspan' in td.attrs:
            colspan = int(td.attrs['colspan'])
            values.extend(['']*colspan - 1)
        if any(values):
          # Only add non-empty rows into the template
          rows.append(values)
  if not column_headers:
    column_headers = rows[0]
    rows = rows[1:]

  # Check for un-equal length rows
  columns = len(column_headers)
  for row in rows:
    columns = max(columns, len(row))
  # Extend short rows to be of equal length
  if len(column_headers) < columns:
      column_headers.extend(['()[]'] * (columns - len(column_headers)))
  for row in rows:
    if len(row) < columns:
      row.extend([''] * (columns - len(row)))

  # Determine how wide each column is (to allow us to format the markdown nicely)
  max_lengths = [3] * columns  # Treat 0 length columns as 3 characters.
  for i, col in enumerate(column_headers):
    max_lengths[i] = max(max_lengths[i], len(col))
  for row in rows:
    for i, col in enumerate(row):
      max_lengths[i] = max(max_lengths[i], len(col))

  # Guess default alignment for data
  alignments = [Alignment.RIGHT] * columns
  for row in rows:
    for i, col in enumerate(row):
      if col and not col.isdigit():
        alignments[i] = Alignment.LEFT

  return Table(
      column_headers=column_headers,
      rows=rows,
      max_col_lengths=max_lengths,
      alignments=alignments,
  )


_MARKDOWN_TEMPLATE_PRETTY = jinja2.Template('''\
{% for col in column_headers %}{% if not loop.first %} | {% endif %}{{ col.center(max_lengths[loop.index0]) }}{% endfor %}
{% for col in column_headers %}{% if not loop.first %} | {% endif %}{{ '-' * max_lengths[loop.index0] }}{% endfor %}
{% for row in rows %}\
{% for value in row %}{% if not loop.first %} | {% endif %}{% if right_align[loop.index0] %}{{ value.rjust(max_lengths[loop.index0]) }}{% else %}{{ value.ljust(max_lengths[loop.index0]) }}{% endif %}{% endfor %}
{% endfor %}
''')
_MARKDOWN_TEMPLATE = jinja2.Template('''\
{% for col in column_headers %}{% if not loop.first %} | {% endif %}{{ col }}{% endfor %}
{% for col in column_headers %}{% if not loop.first %} | {% endif %}{{ '-' * 3 }}{% endfor %}
{% for row in rows %}\
{% for value in row %}{% if not loop.first %} | {% endif %}{{ value }}{% endfor %}
{% endfor %}
''')
def generate_markdown(table):
  if FLAGS.pretty_print:
    template = _MARKDOWN_TEMPLATE_PRETTY
  else:
    template = _MARKDOWN_TEMPLATE
  return template.render(
      column_headers=table.column_headers,
      rows=table.rows,
      max_lengths=table.max_col_lengths,
      right_align=[alignment == Alignment.RIGHT for alignment in table.alignments],
  )


def main(argv):
  # Parse the document passed on stdin
  with open(FLAGS.input_filename, 'rb') as fp:
    soup = bs4.BeautifulSoup(fp, 'html.parser')
  assert soup is not None

  table = parse_doc(soup)
  sys.stdout.write(generate_markdown(table))

if __name__ == '__main__':
  app.run(main)
