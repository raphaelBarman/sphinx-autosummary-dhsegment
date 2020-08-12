__version__ = '1.0'

import importlib.util
import inspect
import pkgutil
import posixpath
import re
from typing import List, Tuple

import sphinx.ext.autosummary.generate as generate
from dh_segment_torch.config import Registrable
from docutils import nodes
from docutils.nodes import Node
from docutils.statemachine import StringList
from sphinx import addnodes
from sphinx.ext.autodoc.directive import DocumenterBridge, Options
from sphinx.ext.autodoc.importer import import_module
from sphinx.ext.autosummary import autosummary_table, get_import_prefixes_from_env, Autosummary, import_by_name, \
    autosummary_toc
from sphinx.locale import __
from sphinx.util import logging, rst
from sphinx.util.docutils import switch_source_input
from sphinx.util.matching import Matcher

ignore_modules = set()
orig_find_autosummary_in_lines = generate.find_autosummary_in_lines
logger = logging.getLogger(__name__)


def get_package_modules(pkgname):
    """Returns a list of module names within the given package."""
    if pkgname in ignore_modules:
        return []

    spec = importlib.util.find_spec(pkgname)
    if not spec:
        logger.warning("Failed to find module {0}".format(pkgname))
        return []

    path = spec.submodule_search_locations

    if not path:
        # This is not a package, but a module.
        # (Fun fact: if we don't return here, we will start importing all the
        # modules on sys.path, which will have all sorts of hilarious effects
        # like reading out the Zen of Python and opening xkcd #353 in the web
        # browser.)
        return []

    names = []
    for importer, modname, ispkg in pkgutil.iter_modules(path):
        fullname = pkgname + '.' + modname
        if fullname in ignore_modules:
            continue

        # Try importing the module; if we can't, then don't add it to the list.
        try:
            import_module(fullname)
        except ImportError:
            logger.exception("Failed to import {0}".format(fullname))
            continue

        names.append(fullname)

    return names


def find_autosummary_in_lines(lines, module=None, filename=None):
    """Overrides the autosummary version of this function to dynamically expand
    an autosummarydhsegment directive into a regular autosummary directive."""

    autosummarydhsegment_re = \
        re.compile(r'^(\s*)\.\.\s+autosummarydhsegment::\s*([A-Za-z0-9_.]+)\s*$')

    lines = list(lines)
    new_lines = []

    while lines:
        line = lines.pop(0)
        m = autosummarydhsegment_re.match(line)
        if m:
            base_indent = m.group(1)
            name = m.group(2).strip()

            new_lines.append(base_indent + '.. autosummary::')

            # Pass on any options.
            while lines:
                line = lines.pop(0)

                if line.strip() and not line.startswith(base_indent + " "):
                    # Deindented line, so end of the autosummary block.
                    break

                new_lines.append(line)

            if new_lines[-1].strip():
                new_lines.append("")

            for subname in get_package_modules(name):
                new_lines.append(base_indent + "   " + subname)

            new_lines.append("")

        new_lines.append(line)

    return orig_find_autosummary_in_lines(new_lines, module, filename)


def find_config_type(obj):
    type_ = None
    default = None
    if issubclass(obj, Registrable):
        method_resolution_order = inspect.getmro(obj)
        for base_class in method_resolution_order:
            if issubclass(base_class, Registrable) and base_class is not Registrable:
                try:
                    type_ = base_class.get_type(obj)
                    default = base_class.default_implementation
                except KeyError:
                    pass
    if type_:
        if type_ == 'default':
            return None
        if type_ == default:
            type_ = f"**{type_}**"
        else:
            type_ = f"*{type_}*"
    return type_

class Autosummarydhsegment(Autosummary):
    """Extends Autosummary to add a column with config name if it exists
    It takes a single argument, the name of the package."""

    def run(self) -> List[Node]:
        self.bridge = DocumenterBridge(self.env, self.state.document.reporter,
                                       Options(), self.lineno, self.state)

        names = [x.strip().split()[0] for x in self.content
                 if x.strip() and re.search(r'^[~a-zA-Z_]', x.strip()[0])]
        items = self.get_items(names)
        nodes = self.get_table(items)

        if 'toctree' in self.options:
            dirname = posixpath.dirname(self.env.docname)

            tree_prefix = self.options['toctree'].strip()
            docnames = []
            excluded = Matcher(self.config.exclude_patterns)
            filename_map = self.config.autosummary_filename_map
            for name, sig, summary, real_name, _ in items:
                real_name = filename_map.get(real_name, real_name)
                docname = posixpath.join(tree_prefix, real_name)
                docname = posixpath.normpath(posixpath.join(dirname, docname))
                if docname not in self.env.found_docs:
                    if excluded(self.env.doc2path(docname, None)):
                        msg = __('autosummary references excluded document %r. Ignored.')
                    else:
                        msg = __('autosummary: stub file not found %r. '
                                 'Check your autosummary_generate setting.')

                    logger.warning(msg, real_name, location=self.get_source_info())
                    continue

                docnames.append(docname)

            if docnames:
                tocnode = addnodes.toctree()
                tocnode['includefiles'] = docnames
                tocnode['entries'] = [(None, docn) for docn in docnames]
                tocnode['maxdepth'] = -1
                tocnode['glob'] = None
                tocnode['caption'] = self.options.get('caption')

                nodes.append(autosummary_toc('', '', tocnode))
        if 'toctree' not in self.options and 'caption' in self.options:
            logger.warning(__('A captioned autosummary requires :toctree: option. ignored.'),
                           location=nodes[-1])

        return nodes
    def get_items(self, names: List[str]) -> List[Tuple[str, str, str, str, str]]:
        prefixes = get_import_prefixes_from_env(self.env)

        items = super().get_items(names)
        new_items = []
        for name, item in zip(names, items):
            if name.startswith('~'):
                name = name[1:]
            real_name, obj, parent, modname = import_by_name(name, prefixes=prefixes)
            config_type = find_config_type(obj)
            new_items.append((item[0], item[1], item[2], item[3], config_type))
        return new_items

    def get_table(self, items: List[Tuple[str, str, str, str, str]]) -> List[Node]:
        """Generate a proper list of table nodes for autosummary:: directive.
        *items* is a list produced by :meth:`get_items`.
        """

        has_config_type = any([item[-1] is not None for item in items])
        if has_config_type:
            n_cols = 3
        else:
            n_cols = 2

        table_spec = addnodes.tabular_col_spec()
        table_spec['spec'] = r'\X{1}{2}\X{1}{2}'

        table = autosummary_table('')
        real_table = nodes.table('', classes=['longtable'])
        table.append(real_table)
        group = nodes.tgroup('', cols=n_cols)
        real_table.append(group)
        group.append(nodes.colspec('', colwidth=10))
        if has_config_type:
            group.append(nodes.colspec('', colwidth=10))
        group.append(nodes.colspec('', colwidth=90))

        head = nodes.thead('')
        cols = ["Class name", "type", "Summary"]
        if not has_config_type:
            del cols[1]
        row = nodes.row('')
        source, line = self.state_machine.get_source_and_line()
        for text in cols:
            node = nodes.paragraph('')
            vl = StringList()
            vl.append(text, '%s:%d:<autosummary>' % (source, line))
            with switch_source_input(self.state, vl):
                self.state.nested_parse(vl, 0, node)
                try:
                    if isinstance(node[0], nodes.paragraph):
                        node = node[0]
                except IndexError:
                    pass
                row.append(nodes.entry('', node))
        head.append(row)
        group.append(head)

        body = nodes.tbody('')
        group.append(body)

        def append_row(*column_texts: str) -> None:
            row = nodes.row('')
            source, line = self.state_machine.get_source_and_line()
            for text in column_texts:
                node = nodes.paragraph('')
                vl = StringList()
                vl.append(text, '%s:%d:<autosummary>' % (source, line))
                with switch_source_input(self.state, vl):
                    self.state.nested_parse(vl, 0, node)
                    try:
                        if isinstance(node[0], nodes.paragraph):
                            node = node[0]
                    except IndexError:
                        pass
                    row.append(nodes.entry('', node))
            body.append(row)

        for name, sig, summary, real_name, config_type in items:
            qualifier = 'obj'
            if 'nosignatures' not in self.options:
                col1 = ':%s:`%s <%s>`\\ %s' % (qualifier, name, real_name, rst.escape(sig))
            else:
                col1 = ':%s:`%s <%s>`' % (qualifier, name, real_name)
            col2 = summary
            if has_config_type:
                col3 = config_type if config_type else ""
                append_row(col1, col3, col2)
            else:
                append_row(col1, col2)
        return [table_spec, table]






def on_config_inited(app, config):
    for mod in config.autosummary_mock_imports:
        ignore_modules.add(mod)


def setup(app):
    generate.find_autosummary_in_lines = find_autosummary_in_lines


    app.add_directive('autosummarydhsegment', Autosummarydhsegment)
    app.connect('config-inited', on_config_inited)

    app.add_config_value('autosummary_filename_map', {}, 'html')


    return {
        'version': __version__,
        'parallel_read_safe': True,
        'parallel_write_safe': True,
    }
