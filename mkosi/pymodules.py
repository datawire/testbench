import importlib
import inspect
from types import ModuleType
from typing import Any, Callable, List, cast


def list_imports(module: ModuleType) -> List[str]:
    """List all of the other modules that `module` imports
    (non-recursively).

    """

    def module_name(v: Any) -> str:
        if hasattr(v, '__module__'):
            return cast(str, getattr(v, '__module__'))
        if hasattr(v, '__name__'):
            return cast(str, getattr(v, '__name__'))
        assert False

    def hasmodule(obj: object) -> bool:
        return inspect.ismodule(obj) or hasattr(obj, '__module__')

    def is_self_or_child(name: str) -> bool:
        return name == module.__name__ or name.startswith(module.__name__+'.')

    return [module_name(v) for _, v in inspect.getmembers(module, hasmodule) if not is_self_or_child(module_name(v))]

def order_modules(module_names: List[str], predicate: Callable[[str], bool], done: List[str]=[], stack: List[str]=[]) -> List[str]:
    """Given a single module name, generate a list of module names that
    will be nescessary to import it.

    Requirements:

     - Parent packages must be listed before any of their child
       modules may be listed

     - Importees must be listed before their importers
    """

    for module_name in module_names:
        if module_name in done:
            continue
        if not predicate(module_name):
            continue
        if module_name in stack:
            raise RuntimeError("order_modules: Cycle detected: %s" % (stack+[module_name]))

        if '.' in module_name:
            done = order_modules([module_name.rsplit('.', 1)[0]], predicate, done, stack+[module_name])
        deps = list_imports(importlib.import_module(module_name))
        done = order_modules(deps, predicate, done, stack+[module_name])
        done = done+[module_name]

    return done
