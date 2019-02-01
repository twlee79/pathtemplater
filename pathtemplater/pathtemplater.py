#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Call the constructor to create a `PathTemplater` object. Note that the
constructor is designed to create a certain 'type' of `PathTemplater`, so
returns an 'empty' `PathTemplater` object that cannot be formatted.

```
>>> path_templater = PathTemplater()
>>> path_templater.use()
Traceback (most recent call last):
...
ValueError: Cannot use() PathTemplater - not fully initialized

```

To create an initialized `PathTemplater` object, call the `create()`
or `create_fromparts()` on the original object. The object can then be formatted
with `use()` or  by converting to string:

```

>>> foobar_templater = path_templater.create("foo/bar/myfile.foobar")

>>> foobar_templater.use()
'foo/bar/myfile.foobar'

>>> str(foobar_templater)
'foo/bar/myfile.foobar'

```

`repr()` outputs the full details of the `PathTemplater` object:

```
>>> print(repr(foobar_templater))
PathTemplater:
 top directory: default ""
 directory: "foo/bar"
 filename template: "myfile"
 filename affix: ""
 suffix: ".foobar"
 alternate suffix: None "" (append? False)
 format dictionary: {}
 formatted: foo/bar/myfile.foobar

```

All `PathTemplater` methods to alter object's variables (including `create()`)
do not directly alter these variables, but instead return a *copy* of the
object with the new variables set. This ensures that these methods do not have
side effects.

>>> foobar_templater.new_directory('foo2/bar').use()
'foo2/bar/myfile.foobar'

>>> foobar_templater.use()
'foo/bar/myfile.foobar'

```

As each method returns a derived object, changes can be stacked easily:

```
>>> foobar_templater.new_directory('bar').new_suffix('.bar').use()
'bar/myfile.bar'

```

Derived objects can be stored for further changes later:

>>> bar_templater = foobar_templater.new_directory('bar').new_suffix('.bar')
>>> bar_templater.new_template('yourfile').use()
'bar/yourfile.bar'

Additional power comes from the use of a `PathTemplater` to template
strings using Python string formatting `{placeholder}` syntax, which is
compatible with Snakemake `{wildcard}` syntax. A `PathTemplater` can be
formatted, partly formatted (keeping unused paceholders intact), or
expanded (partly/fully):

```
>>> ab_templater = path_templater.create("foo/bar/{alpha}-{beta}.foobar")
>>> ab_templater.format(alpha = 'xxx', beta = 'yyy')
'foo/bar/xxx-yyy.foobar'
>>> ab_templater.pformat(alpha = 'xxx')
'foo/bar/xxx-{beta}.foobar'
>>> ab_templater.expand(alpha = ['xxx1', 'xxx2'], partial = True)
['foo/bar/xxx1-{beta}.foobar', 'foo/bar/xxx2-{beta}.foobar']
>>> ab_templater.expand(alpha = ['xxx1', 'xxx2'], beta = ['yyy1', 'yyy2'])
['foo/bar/xxx1-yyy1.foobar', 'foo/bar/xxx1-yyy2.foobar', 'foo/bar/xxx2-yyy1.foobar', 'foo/bar/xxx2-yyy2.foobar']

```

'Full' (non-partial) formatting and expansion requires all `{placeholders}` to
be filled:
```
>>> ab_templater.format(alpha = 'xxx')
Traceback (most recent call last):
...
KeyError: 'beta'
>>> ab_templater.expand(alpha = ['xxx1', 'xxx2'])
Traceback (most recent call last):
...
KeyError: 'beta'

```
"""
_PROGRAM_NAME = 'pathtemplater'
# -------------------------------------------------------------------------------
# Author:       Tet Woo Lee
#
# Created:      2019-01-19
# Copyright:    © 2019 Tet Woo Lee
# Licence:      GPLv3
#
# -------------------------------------------------------------------------------

_PROGRAM_VERSION = '1.0.0.dev3'
# -------------------------------------------------------------------------------
# ### Change log
#
# version 1.0.0.dev3 2019-02-01
# : Altered method of adding presets, now supports arbitrary function calls.
#
# version 1.0.0.dev2 2019-02-01
# : Added some additional functions to PathTemplater: remove_affix,
#   get_directory_aspathlib, get_directory, apply_format, add_alt_suffixes,
#   add_preset_formats.
# : preset_formats now supports specifying _topdir and _altsuffix.
#
# version 1.0.0dev1 2019-01-30
# : Working version, partially tested with doctest
# -------------------------------------------------------------------------------



import pathlib
import warnings
import copy
import types
import string
import itertools
import collections

class _AsIsFormat:
    """
    For partial formatting of strings, based on https://ideone.com/xykV7R

    This is a class that formats itself 'as-is', i.e. with original format string
    e.g. `{key:%.0f} -> {key:%.0f}` or `{key[index]} -> {key[index]}`

    For use with `_PartialDict`
    """
    def __init__(self, key):
        self.key = key
    def __getitem__(self, index):
    	return "{" + self.key + "[" + index + "]}"
    def __format__(self, format_spec):
    	return "{" + self.key + ((":" + format_spec) if format_spec else "") + "}"

class _PartialDict(dict):
    """
    Simple derived dict that returns an as-is formatter for missing keys.

    To partially format a string use, e.g.:
    `string.Formatter().vformat('{name} {job} {bye}',(),_PartialDict(name="me", job="you"))`
    which gives `me you {bye}`
    """
    def __missing__(self, key):
        return _AsIsFormat(key)

class _TrackingPartialDict(_PartialDict):
    """
    Subclass of `_PartialDict` that tracks usage of `__getitem__`.
    """
    def __init__(self, *args, **kwargs):
        self.used_items = set()
        super(_TrackingPartialDict, self).__init__(*args, **kwargs)
    def __getitem__(self, index):
        self.used_items.add(index)
        return super(_TrackingPartialDict, self).__getitem__(index)

class PathTemplater:
    """
    Class for templating paths. Initial written to help template Snakemake
    paths (`input:`, `output:`, `log:`) to provide a easy way to derive new
    paths at each step.

    This class allows Snakemake paths to be built from the following elements
    and certain flags:
        * `directory`: directory for the path, e.g. `out1_aligned`
        * `filename_template`: filename template for the path, may contain
           wildcards as per Snakemake format, e.g. `{sample_name}_{inputid}`
        * `suffix`: filename extension
        * `filename_affix` : (optional) an affix added to then end of the
           filename, useful for using the same `filename_template` but with
           a different affix, e.g. `_trimmed`
        * `is_logdir`: (boolean, default `False`) if `True`, path is placed
          under the top-level directory `logs` (actually the directory
          specified using the `logs_directory` global; `logs` is the
          default read from config file)
        * `is_logfile`: (boolean, default `False`) if `True`, path is placed
          under the `logs` directory and is given the suffix `.logs`
          (replacing any existing suffix, actually the suffix specified using
          the `log_extension` global; `.log` is the default read from config
          file)
        * `format_dict`: (optional) a `dict` containing any wildcards that
          will be expanded

    To use the path as a string, the `use()` member function is called. This
    will generate the path (using a `PathLib.path` object), turn it into a
    string and use values present in the `format_dict` to resolve any
    corresponding wildcards (by name). Partial wildcard expansion is allowed.
    This provides flexibility for use in Snakemake rules - any wildcards that
    Snakemake should resolve itself are simply left unspecified when using the
    `PathTemplater` object.

    To create a path with altered elements or flags, various helper functions
    are provided. Note that this return a *copy* of the original object, so
    the attributes of the original object are not changed (to prevent
    side-effects, the attributes should never be changed after object creation,
    hence all attributes are named as private member variables). `use()` can be
    called with these new objects to generate the paths. Examples of these
    functions are:
        * `end1()`, `end2()`, `end1_2()`: create derived objects with the `end_label`
        wildcard set by `end1_label`, `end2_label` or `end1_2_label`,
        typically `R1`, `R2` or `R1-2`; obviously correctly resolving these
        requires `end_label` in the `filename_template`.
        * `logfile()`, `logdir()`: create derived objects with `is_logfile` or
        `is_logdir` flags set to `True`.
        * `new_directory()`, `new_template()`, `new_affix()`, `new_suffix()`:
        create derived objects with various elements altered.
        * `apply_affix()`: apply the current filename affix (i.e. affix the
        affix) to the `filename_template` permanently.

    As each produces a `PathTemplater` object, these can be stacked to
    give multiple changes. They can also be used to obtain derived paths
    from one rule to another, changing the directory, affix, suffix, etc.
    Example usage might be:
        * `my_template.end1().use()`
        * `my_template.logfile().use()`
        * `my_template.end1_2().logfile().use()`
        * `rule2_output_template = rule1_output_template.new_directory('output2')`
           and then for `rule 2:`, `input: rule1_output_template.use()` and
           `output: rule2_output_template.use()`.

    In addition to use(), additional functions to generating the path are provided:
        * `expand_ends`: generate a dict containing `end_label`: path mappings,
        e.g. `{R1 : path_for_R1, R2 : path_for_R2}`.
        * `pformat(), format, expand` : generate path(s) with partially
        resolved, resolved or expanded wildcards.

    """
    def __init__(self, top_directories = None, alt_suffixes = None,
                 preset_formats = None):
        self._reset_topdir()
        self._reset_altsuffix()
        self._reset_dfs()
        if not top_directories:
            # if no top directories specified, use a default empty top directory
            top_directories = {'default': ''}
        if len(top_directories)==1:
            # if a single top directory provided (or using default)
            # initalize to this top directory directly, as no changing
            # top directory is possible
            self._topdir_name, self._topdir_value = next(iter(top_directories.items() ))
        else:
            # add bound methods to instance to set top directory
            # e.g. self.outputdir() to set to current topdir to 'output' topdir
            # top directory
            # one of these functions must be called after creating the object
            # to initalize the object
            for name, value in top_directories.items():
                setattr(self, PathTemplater._get_settopdir_methodname(name),
                    PathTemplater._set_topdir_boundmethod(self, name, value))
        self.add_alt_suffixes(alt_suffixes)
        self.add_preset_formats(preset_formats)
    def add_alt_suffixes(self, alt_suffixes):
        """
        Add `all_suffixes` to this object.

        >>> foobar_templater = PathTemplater().create("foo/bar/myfile.foobar")
        >>> foobar_templater.add_alt_suffixes({'boobar':'.boobar', 'tar':'+.tar'})
        >>> foobar_templater.boobarfile().use()
        'foo/bar/myfile.boobar'
        >>> foobar_templater.tarfile().use()
        'foo/bar/myfile.foobar.tar'

        """
        if alt_suffixes:
            for name, value in alt_suffixes.items():
                if value[0]=='+':
                    altsuffix_append = True
                    altsuffix = value[1:]
                else:
                    altsuffix_append = False
                    altsuffix = value
                setattr(self, PathTemplater._get_setfilesuffix_methodname(name),
                    PathTemplater._set_altsuffix_boundmethod(self, name, altsuffix, altsuffix_append))
    def add_preset_formats(self,preset_formats):
        """
        Add `preset_formats` to this object, in the format
        ```
        {preset1_name : {preset1_format1 : preset1_format1_value,
                         preset1_format2 : preset1_format2_value,
                         ...
                         },
         preset2_name : {preset2_format1 : preset2_format1_value,
                         ...
                         },
                         ...
        }

        `preset_name` is used as a function name for activating the preset.

        `preset_format_value` can be:

        1) A tuple of a list and dict `([],{})`. If so, then `preset_format`
        is expected to be a callable function within the,
        object that is called using the list and tuple as `*args` and
        `**kwargs`. This is 'callable' format.

        2) A simple value. `preset_format` is used a placeholder name,
        and the `preset_format_value` is the value of the placeholder used to
        when formatting the object. This is equivalent to
        `add_to_dict(preset_format, preset_format_value)`. This is
        'standard' format.

        3) An iterable collection. `preset_format` is used a placeholder name
        and the object is expanded with all values of `preset_format_value`.
        This is 'expandable' format.

        Standard and callable formats can be mixed in the same preset, and
        the preset function will return a copy of the object with any preset
        defined functions called and placeholders add to the format dictionary

        Expandable formats cannot be mixed with callable formats (it can
        be mixed with standard formats). The preset function will return a
        list of formatted strings when the preset function is called.

        Example of adding preset containing only a standard format (`use()`
        converts the returned derived object to a string):

        >>> foobar_templater = PathTemplater().create("foo/bar/myfile_{animal}.foobar")
        >>> foobar_templater.add_preset_formats({'cat': {'animal': 'cat'}})
        >>> foobar_templater.cat().use()
        'foo/bar/myfile_cat.foobar'

        Example of adding a preset containing only an expandable format:
        >>> foobar_templater.add_preset_formats({'all_animals': {'animal': ['cat','dog']}})
        >>> foobar_templater.all_animals()
        ['foo/bar/myfile_cat.foobar', 'foo/bar/myfile_dog.foobar']

        Example of adding preset containing callable formats. First construct a
        templater object with multiple top directories `foo` and `bar`,
        accessed by the `foobardir()` and `boobardir()` functions:
        >>> foobar_templater = PathTemplater({'foobar':'foo','boobar':'boo'}).create("bar/myfile_{animal}.foobar")
        >>> foobar_templater.foobardir().use()
        'foo/bar/myfile_{animal}.foobar'

        Add alternate suffixes to the templater. This allows `.boobar` suffix to
        be used by calling `boobarfile()`. Since this suffix corresponds to the
        `boobar` top directory, using it will alter top directory to that
        defined by `boobardir()`. `tarfile` will append a `.tar` suffix.
        >>> foobar_templater.add_alt_suffixes({'boobar':'.boobar', 'tar':'+.tar'})
        >>> foobar_templater.boobarfile().use()
        'boo/bar/myfile_{animal}.boobar'
        >>> foobar_templater.foobardir().tarfile().use()
        'foo/bar/myfile_{animal}.foobar.tar'

        Create a preset containing a standard format, and two callable formats.
        This effectively stacks storing the value of placeholder `animal` as
        `cat`, a call to the `forbardir()` function and a call to the `tarfile()`
        function in a single call to the `cat_tar_in_foobar()` function:
        >>> foobar_templater.add_preset_formats({'cat_tar_in_foobar' : {'animal' : 'cat', 'foobardir': ([],{}), 'tarfile' : ([],{}) } })
        >>> foobar_templater.cat_tar_in_foobar().use()
        'foo/bar/myfile_cat.foobar.tar'

        Callable preset formats can specify functions in the object that require
        parameters by supplying these in the list or dict provided. Here, the
        `new_template()` function is called with a parameter in the
        `yourfile_template()` preset:
        >>> foobar_templater.add_preset_formats({'yourfile_template' : {'new_template':(['yourfile_{animal}'],{}) }})
        >>> foobar_templater.foobardir().yourfile_template().use()
        'foo/bar/yourfile_{animal}.foobar'

        Callable preset formats cannot be combined with expandable formats:
        >>> foobar_templater.add_preset_formats({'yourfile_template' : {'new_template':(['yourfile_{animal}'],{}), 'all_animals' : ['cat','dog'] }})
        Traceback (most recent call last):
        ...
        ValueError: Cannot use callable with expand()-style format

        An of course, the function specified in the format must exist in the
        object (and be callable with the provided parameters, if any):
        >>> foobar_templater.add_preset_formats({'will_fail' : {'zipfile':([],{})}})
        >>> foobar_templater.will_fail().use()
        Traceback (most recent call last):
        ...
        ValueError: Preset value zipfile:([], {}) provided but could not find function zipfile
        """
        if preset_formats: # TODO add type checking
            for preset_name, format_dict in preset_formats.items():
                # check if any of the provided values in format_dict is a collection
                have_iterable = False
                have_callable = False
                for format_value in format_dict.values():
                    if isinstance(format_value, collections.Iterable) and not isinstance(format_value, str):
                        # ([],{}) -> specifies funciton to call
                        if PathTemplater._is_funcparams_tuple(format_value):
                            have_callable = True
                        else:
                            have_iterable = True
                if have_iterable:
                    if have_callable:
                        raise ValueError("Cannot use callable with expand()-style format")
                    the_func = PathTemplater._preset_expand_boundmethod
                else:
                    if have_callable:
                        the_func = PathTemplater._preset_addtodict_withcalls_boundmethod
                    else:
                        the_func = PathTemplater._preset_addtodict_boundmethod
                setattr(self, preset_name, the_func(self, **format_dict))
    def _reset_topdir(self):
        """
        Reset top directory settings.
        """
        self._topdir_name = None # internal name of topdir
        self._topdir_value = None # actual value of topdir, used for formatting paths

    def _reset_altsuffix(self):
        """
        Reset any alternative suffix settings.
        """
        self._altsuffix_name = None # internal name of suffix
        self._altsuffix_value = "" # actual value of suffix
        self._altsuffix_append = False # whether to append suffix (otherwise replace)
    def _reset_dfs(self):
        """
        Reset directory, filename template, suffix, affix and format_sict settings.
        """
        self._directory = None
        self._filename_template = None
        self._suffix = ""
        self._filename_affix = ""
        self._format_dict = {}
    def _is_initialized(self):
        """
        Return `True` if this object has been fully initialized with a
        top directory, directory and filename template.
        """
        return self._topdir_name is not None and self._directory is not None and self._filename_template is not None
    def create(self, path, filename_affix = "", format_dict = {}):
        """
        Initialize an empty `PathTemplater` object, generating directory,
        filename template and suffix by splitting `path`, and optional
        `filename_affix` and `format_dict` provided. Cannot be used on a
        `PathTemplater` object that has already been initialized.

        Returns `self` to allow easy stacking of function calls.
        """
        if self._is_initialized():
            raise ValueError("Cannot use create() with initialized PathTemplater")
        the_path = pathlib.Path(path)
        directory = the_path.parent
        suffix = "".join(the_path.suffixes) # want all suffixes combined
        filename_template = the_path.name.replace(suffix,"") # stem removes only 1 suffix
        return self.create_fromparts(directory, filename_template, suffix, filename_affix,
                           format_dict)
    def create_fromparts(self, directory, filename_template, suffix = "",
        filename_affix = "", format_dict = {}):
        """
        Initialize a new `PathTemplater` object from `directory`,
        `filename_template` and optional `suffix`,
        `filename_affix` and `format_dict`. Cannot be used on a
        `PathTemplater` object that has already been initialized.

        Returns `self` to allow easy stacking of function calls.
        """
        if self._is_initialized():
            raise ValueError("Cannot use create_fromparts() with initialized PathTemplater")
        new_obj = copy.deepcopy(self)
        new_obj._directory = directory
        new_obj._filename_template = filename_template
        new_obj._suffix = suffix
        new_obj._filename_affix = filename_affix
        new_obj._format_dict.update(format_dict)
        return new_obj
    @staticmethod
    def _is_funcparams_tuple(x):
        """
        Return `True` if `x` is tuple of list and dict, i.e `([],{})`.
        """
        return isinstance(x,tuple) and len(x)==2 and isinstance(x[0],list) and isinstance(x[1],dict)
    @staticmethod
    def _get_settopdir_methodname(topdir_name):
        """
        Return the name of the method name to set top directory to `topdir_name`
        e.g. `outputdir()` for setting to `output`.
        """
        return topdir_name+'dir'
    def _get_setfilesuffix_methodname(altsuffix_name):
        """
        Return the name of the method name to set suffix to `altsuffix_name`
        e.g. `logfile()` for setting to `log`.
        """
        return altsuffix_name+'file'
    @staticmethod
    def _bound_method(function, instance):
        """
        Return a `function` as bound method of `instance`.
        """
        return types.MethodType(function, instance)
            # Python 3: types.MethodType(function, instance)
    @staticmethod
    def _set_topdir(cur_obj, topdir_name, topdir_value):
        """
        Generate a copy of `cur_obj` with `_topdir_name, _topdir_value` member
        variables set to `topdir_name, topdir_value`.
        """
        if cur_obj._topdir_name == topdir_name:
            warnings.warn("setting top directory on PathTemplater that is already set to same top directory {}".format(topdir_name))
        new_obj = copy.deepcopy(cur_obj)
        new_obj._topdir_name = topdir_name
        new_obj._topdir_value = topdir_value
        return new_obj
    @staticmethod
    def _set_topdir_boundmethod(instance, topdir_name, topdir_value):
        """
        Generate bound method of `instance._set_topdir(topdir_name, topdir_value)`.
        """
        return PathTemplater._bound_method(lambda self: PathTemplater._set_topdir(self, topdir_name, topdir_value), instance)
    @staticmethod
    def _set_altsuffix(cur_obj, altsuffix_name, altsuffix_value, altsuffix_append):
        """
        Generate a copy of `cur_obj` with `_altsuffix_name, _altsuffix_value, _altsuffix_append`
        member   set to `altsuffix_name, altsuffix_value, altsuffix_append`.
        """
        if cur_obj._altsuffix_name == altsuffix_name:
            warnings.warn("setting suffix on PathTemplater that is already set to same suffix {}".format(suffix_name))
        new_obj = None
        # suffix and topdir matching...
        if cur_obj._topdir_name != altsuffix_name:
            # if new altsuffix_name is not the same as current topdir_name...
            set_topdir_method = getattr(cur_obj, PathTemplater._get_settopdir_methodname(altsuffix_name),None)
            if set_topdir_method is not None:
                # and, we have a method in the object to set topdir with same name
                # e.g. logdir() when altsuffix_name is log
                # call that method to generate a new_obj with suffix changed appropriately
                new_obj = set_topdir_method()
        if new_obj is None: # otherwise just create object copy
            new_obj = copy.deepcopy(cur_obj)
        new_obj._altsuffix_name = altsuffix_name
        new_obj._altsuffix_value = altsuffix_value
        new_obj._altsuffix_append = altsuffix_append
        return new_obj
    @staticmethod
    def _set_altsuffix_boundmethod(instance, altsuffix_name, altsuffix_value, altsuffix_append):
        """
        Generate bound method of `instance._set_suffix(altsuffix_name, altsuffix_value, altsuffix_append)`.
        """
        return PathTemplater._bound_method(lambda self: PathTemplater._set_altsuffix(self, altsuffix_name, altsuffix_value, altsuffix_append), instance)
    def _add_to_dict_with_calls(self,**kwargs):
        """
        Generate a copy of the object that contains `**kwargs` added to the
        `format_dict` for wildcard resolving. Special processing to parse
        parameters with `([],{})` values, these will add calls to
        corresponding function named by parameter name, and with provided
        values as `*args` and `**kwargs`

        """
        new_obj = copy.deepcopy(self)
        used_items = set()
        for param,param_value in kwargs.items():
            if PathTemplater._is_funcparams_tuple(param_value):
                the_funcname = param
            else:
                the_funcname = None
            if the_funcname:
                the_func = getattr(new_obj, the_funcname, None)
                if the_func is None:
                    raise ValueError("Preset value {}:{} provided but could not find function {}".format(param, param_value, the_funcname))
                call_args,call_kwargs= param_value
                new_obj = the_func(*call_args,**call_kwargs)
                used_items.add(param)
        for item in used_items:
            kwargs.pop(item, None)
        if not kwargs: return new_obj
        return new_obj.add_to_dict(**kwargs)
    @staticmethod
    def _preset_addtodict_boundmethod(instance, **kwargs):
        return PathTemplater._bound_method(lambda self: PathTemplater.add_to_dict(self, **kwargs), instance)
    @staticmethod
    def _preset_addtodict_withcalls_boundmethod(instance, **kwargs):
        return PathTemplater._bound_method(lambda self: PathTemplater._add_to_dict_with_calls(self, **kwargs), instance)
    @staticmethod
    def _preset_expand_boundmethod(instance, **kwargs):
        return PathTemplater._bound_method(lambda self: PathTemplater.expand(self, partial = True, **kwargs), instance)
    def get_directory_aspathlib(self):
        """
        Generate a path consisting only of the  directory and directory
        components of the path as a pathlib.Path.

        >>> foobar_templater = PathTemplater().create("foo/bar/myfile.foobar")
        >>> foobar_templater.get_directory_aspathlib() == pathlib.Path('foo/bar')
        True

        """
        if not self._is_initialized():
            raise ValueError("Cannot use() PathTemplater - not fully initialized")
        path = pathlib.Path(self._topdir_value)
        return path / self._directory
    def get_directory(self):
        """
        Generate a path consisting only of the  directory and directory
        components of the path as a string.

        >>> foobar_templater = PathTemplater().create("foo/bar/myfile.foobar")
        >>> foobar_templater.get_directory()
        'foo/bar'

        """
        return self.get_directory_aspathlib().__str__()
    def use(self):
        """
        Generates the path as a string, replacing any wildcards for which
        values have been provided in the `format_dict`.

        >>> foobar_templater = PathTemplater().create("foo/bar/myfile_{foo}.foobar", filename_affix = "_extrabar", format_dict = {'foo': "oof"})
        >>> foobar_templater.use()
        'foo/bar/myfile_oof_extrabar.foobar'

        """
        path = self.get_directory_aspathlib() / self._get_combined_template_affix()
        if self._altsuffix_name is not None:
            if self._altsuffix_append:
                suffix = self._suffix + self._altsuffix_value
            else:
                suffix = self._altsuffix_value
        else:
            suffix = self._suffix
        if suffix:
            path = path.with_suffix(''.join(path.suffixes) + suffix)
            # with_suffix replaces any existing suffix, this ensures we
            # add to any existing suffix on template
        template = path.__str__()
        return string.Formatter().vformat(template, (), _PartialDict(self._format_dict))
    def _get_combined_template_affix(self):
        return self._filename_template+self._filename_affix
    def add_to_dict(self,**kwargs):
        """
        Generate a copy of the object that contains `**kwargs` added to the
        `format_dict` for wildcard resolving.
        """
        new_obj = copy.deepcopy(self)
        if len(kwargs)==0:
            warnings.warn("add_to_dict() called on PathTemplater with no arguments")
        else:
            new_obj._format_dict.update(**kwargs)
        return new_obj
    def clear_dict(self):
        """
        Generate a copy of the object with `format_dict` cleared
        """
        return self.new_directory({})
    def new_directory(self, new_directory):
        """
        Generate a copy of the object with `format_dict` replaced by `new_directory`.
        """
        new_obj = copy.deepcopy(self)
        new_obj._directory = new_directory
        return new_obj
    def new_template(self, new_template):
        """
        Generate a copy of the object with `filename_template` replaced by
        `new_template`.
        """
        new_obj = copy.deepcopy(self)
        new_obj._filename_template = new_template
        return new_obj
    def remove_affix(self):
        """
        Generate a copy of the object with `filename_affix` replaced by
        an empty string.

        >>> foobar_templater = PathTemplater().create("foo/bar/myfile.foobar", filename_affix="_extrabar")
        >>> foobar_templater.use()
        'foo/bar/myfile_extrabar.foobar'

        >>> foobar_templater.remove_affix().use()
        'foo/bar/myfile.foobar'

        """
        return self.new_affix("")
    def new_affix(self, new_affix):
        """
        Generate a copy of the object with `filename_affix` replaced by
        `new_affix`.

        >>> foobar_templater = PathTemplater().create("foo/bar/myfile.foobar")
        >>> foobar_templater.use()
        'foo/bar/myfile.foobar'

        >>> foobar_templater.new_affix("_extrabar").use()
        'foo/bar/myfile_extrabar.foobar'
        """
        new_obj = copy.deepcopy(self)
        new_obj._filename_affix = new_affix
        return new_obj
    def apply_affix(self):
        """
        Generate a copy of the object with `filename_affix` permanently affixed
        to the `filename_template`. Obviously, once affixed, the old
        `filename_affix` can no longer be removed. but a new (additional)
        `filename_affix` could be added.
        """
        new_obj = copy.deepcopy(self)
        new_obj._filename_template = new_obj._get_combined_template_affix()
        new_obj._filename_affix = ""
        return new_obj
    def new_suffix(self, new_suffix):
        """
        Generate a copy of the object with `suffix` replaced by `new_suffix`.
        """
        new_obj = copy.deepcopy(self)
        new_obj._suffix = new_suffix
        return new_obj
    def no_suffix(self):
        """
        Generate a copy of the object with no `suffix`.
        """
        return self.new_suffix(None)
    def reset_altsuffix(self):
        """
        Generate a copy of the object with alternative suffix reset.
        """
        new_obj = copy.deepcopy(self)
        new_obj._reset_altsuffix()
        return new_obj
    def expand_ends(self):
        """
        Generate a dict mapping each `end_label` to its path, e.g.
        `{'R1' : 'out/{sample_name}_R1.ext', 'R2' : 'out/{sample_name}_R2.ext'}`
        """
        return dict(zip(end_labels,
            map(lambda x: self._end(x).use(), end_labels)))
    def apply_format(self, **kwargs):
        """
        Generate a copy of the object with any placeholders in
        filename template and affix replaced by any existing items
        in the format dictionary. Any used items are removed from
        the format dictionary.

        >>> foobar_templater = PathTemplater().create("foo/bar/myfile-{animal}-{food}.foobar",filename_affix='_{person}')
        >>> foobar_templater.apply_format(animal='cat',person='george').use()
        'foo/bar/myfile-cat-{food}_george.foobar'
        """
        new_obj = self.add_to_dict(**kwargs)
        tracking_dict = _TrackingPartialDict(new_obj._format_dict)
        new_obj._filename_template = string.Formatter().vformat(new_obj._filename_template, (),tracking_dict)
        new_obj._filename_affix = string.Formatter().vformat(new_obj._filename_affix, (),tracking_dict)
        for item in tracking_dict.used_items:
            new_obj._format_dict.pop(item, None) # supplying default prevents KeyError for misisng items
        return new_obj
    def pformat(self, **kwargs):
        """
        Generate the path while partially formatting with the named placeholders
        given in `**kwargs`. For example, `pformat(param1 = 'A')` will format
        the template `{param1}-{param2}` as `A-{param2}`.

        The same as calling `add_to_dict(**kwargs).use()`.
        """
        return self.add_to_dict(**kwargs).use()
    def format(self, **kwargs):
        """
        Generate the path while formatting with the named placeholders
        given in `**kwargs`. For example, `pformat(param1 = 'A', param2 'x')`
        will format the template `{param1}-{param2}` as `A-x`.

        All named placeholders must be supplied in `**kwargs`. As per the
        Python `str.format` method, a `KeyError` is produced if any named
        placeholder is missing.
        """
        return self.use().format(**kwargs)
    def expand(self, combinator = itertools.product, partial = False, **kwargs):
        """
        Generate all combinations of the path using collections of values for
        named placeholders provided in `**kwargs` Combinations generated with
        using `combinator` (default: `itertools.product`).

        For example `expand(param1 = ('A','B'), param2 = 'x','y')`
        will expand the template `{param1}-{param2}` as
        `['A-x', 'A-y', 'B-x', 'B-y']`. Alternative, using `combinator = zip`
        will give `['A-x', 'B-y']`.

        `partial = True` allows partial formatting, i.e. some format
        placeholders to be missing, otherwise missing values for named
        placeholders results in a `KeyError`.
        """

        if partial:
            format_func = lambda combination: self.add_to_dict(**combination).use()
        else:
            format_func = lambda combination: self.use().format(**combination)

        def expand_kwargs(kwargs):
            # generator that yields keyx: [item1, item2, item3] expanded
            # to [(keyx, item1), (keyx: item2), (keyx: item3)]
            for key, value in kwargs.items():
                if isinstance(value, str) or not isinstance(value, collections.Iterable):
                    yield [(key, value)]
                else:
                    yield [(key, item) for item in value]
        expanded_kwargs = list(expand_kwargs(kwargs))
        return [format_func(combination)
                for combination in map(dict, combinator(*expanded_kwargs))]
    def __str__(self):
        return self.use()
    def _getattrs(self, attrs):
        if isinstance(attrs ,str):
            return [getattr(self, attrs)]
        return (getattr(self, attr) for attr in attrs)
    def __repr__(self):
        repr_format_values = [
            (' top directory: {} "{}"', ["_topdir_name", "_topdir_value"]),
            (' directory: "{}"', ["_directory"]),
            (' filename template: "{}"', ["_filename_template"]),
            (' filename affix: "{}"', ["_filename_affix"]),
            (' suffix: "{}"', ["_suffix"]),
            (' alternate suffix: {} "{}" (append? {})', ["_altsuffix_name", "_altsuffix_value", "_altsuffix_append"]),
            (' format dictionary: {}', ["_format_dict"])
        ]
        #return "\n".join(repr_format.format(self._getattrs(repr_values)) for repr_format,repr_values in repr_format_values)
        return "PathTemplater:\n"+ \
               "\n".join(repr_format.format(*map(lambda x: getattr(self,x),repr_values)) for repr_format,repr_values in repr_format_values)+ \
               "\n formatted: "+str(self)


"""
`top_directories` is an optional `dict` of alternative top-level directories
for placing the path within, given as `topdir_name : topdir_value` mappings.
Methods named `{topdir_name}dir()` are added to the object for each top directory
provided. Each of these methods provides a way to produce a derived object set
to use a different top directory. If >1 top directory is provided, the object
is not properly initialized until one of these methods is called, because
there is no 'default' top directory.

For example `top_directories = {'output' : 'out', 'log' : 'logs' }` will
create the methods `outputdir()` and `logdir()` in the object. Calling
`outputdir()` generates a derived `PathTemplater` object with all parameters
frm the original object, except top directory set to `out`.

`alt_suffixes` is an optional `dict` providing alternative suffixes (filename
extensions), given as `altsuffix_name : altsuffix_value` mappings.
Methods named `{altsuffix_name}file()` are added to the object for each
alternative suffix provided. Each of these methods provides a way of producing
a derived object to produce a path for a different file type. If the
`altsuffix_value` begins with a `+` character, the new suffix is appended to
any existing suffix(es) on the filename. Otherwise, the suffix replaces the
current `suffix` in the object when the path is formatted. As suffix operations
use the `pathlib` package, they must begin with a dot. If an `altsuffix_name`
is identical to a provided `topdir_name`, calling the associated `file()`
method will also set the top directory at the same time. This behaviour is
useful, for example, to produce paths for log files with a certain extension
(`.log`) and collected in a different top directory (`logs`).

For example `alt_suffixes = {'log' : '.log', 'complete', '+.complete'}` will
create methods `logfile()` and `completefile()` in the object. Calling
`logfile()` generates a derived object with top directory set to the `log`
directory (if set using `top_directories`) and with suffix changed to `.log`.
Calling `completefile()` generates a derived object with suffix `.complete`
appended to the path (and no change in top directory, assuming no `complete`
top directory was provided).

`preset_formats` is an optional `dict` providing preset path 'formats',
given as `preset_name : {preset_format_dict}`. The `preset_format_dict` is
a `dict` containing `placeholder : placeholder_value` and/or
`placeholder : [placeholder_values]` mappings. Methods named `{preset_name}()`
are added to the object for each preset provided.
If the only individual `placeholder_value`s provided for all placeholders,
calling such a method generates a derived object with the `preset_format_dict`
added to the format dictionary (containing stored `placeholder:value` mappings).
If a collection (e.g. list) of placeholder values is provided for any of the
placeholders, calling such a method partially expands the placeholders for
all combinations provided.

For example...

"""

if __name__ == "__main__":
    import doctest
    doctest.testmod()
