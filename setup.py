#!/usr/bin/env python

from setuptools import setup

setup(
    name='tsukasa-au-scripts',
    version='1.0',
    description='A random collection of useful scripts',
    license='apache2',
    install_requires = [
      'jinja2',
      'absl-py',
      'beautifulsoup4',
    ],
    scripts = [
      'table2markdown/table2markdown.py'
    ],
)
