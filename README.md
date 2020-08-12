sphinx-autosummary-dhsegment
============================

This is a [Sphinx](https://www.sphinx-doc.org/) extension to generate API documentation for the config system of dhSegment using the built-in autosummary extension.

The extension makes it possible to lookup the config type name of a class and add a third column to the autosummary usual two columns.

Usage
-----

1. Install the module using pip.
```
pip install sphinx-autosummary-dhsegment
```
2. Enable it in `conf.py`.
```python
extensions = ['sphinx.ext.autosummary', 'sphinx_autosummary_dhsegment']

autosummary_generate = True
```
3. When needed, replace `autosummary` by `autosummarydhsegment`
