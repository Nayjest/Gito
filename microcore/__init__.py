from enum import Enum
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from jinja2 import Template


class ApiType(Enum):
    NONE = 0
    OPENAI = 1
    ANTHROPIC = 2
    GOOGLE = 3
    AZURE = 4


class _UI:
    bright = ""
    reset = ""
    dim = ""

    def _identity(self, s: str) -> str:
        return str(s)

    def green(self, s: str) -> str:
        return self._identity(s)

    def yellow(self, s: str) -> str:
        return self._identity(s)

    def cyan(self, s: str) -> str:
        return self._identity(s)

    def red(self, s: str) -> str:
        return self._identity(s)

    def blue(self, s: str) -> str:
        return self._identity(s)

    def gray(self, s: str) -> str:
        return self._identity(s)

    def white(self, s: str) -> str:
        return self._identity(s)

    def error(self, s: str) -> None:
        print(self._identity(s))

    def warning(self, s: str) -> None:
        print(self._identity(s))

    def ask_choose(self, prompt: str, choices, default=None):
        # choices may be dict, list or other iterable; return first key/value
        if isinstance(choices, dict):
            # return first key
            for k in choices:
                return k
        try:
            return list(choices)[0]
        except Exception:
            return default

    def ask_yn(self, prompt: str) -> bool:
        return False

    def ask_non_empty(self, prompt: str) -> str:
        return ""


ui = _UI()


_CONFIG = {}


def configure(**kwargs):
    """Minimal configure shim used by tests."""
    _CONFIG.update(kwargs)


def get_config(key, default=None):
    return _CONFIG.get(key, default)


def config():
    class C:
        pass

    c = C()
    c.MODEL = _CONFIG.get("MODEL", None)
    c.MAX_CONCURRENT_TASKS = _CONFIG.get("MAX_CONCURRENT_TASKS", None)
    return c


def tpl(template: str, **kwargs) -> str:
    """Render a jinja2 template from configured template paths."""
    search_paths = _CONFIG.get("PROMPT_TEMPLATES_PATH", []) or []
    # ensure Path-like strings
    loaders = [str(p) for p in search_paths]
    if not loaders:
        loaders = [str(Path(__file__).parent / "tpl")]
    env = Environment(loader=FileSystemLoader(loaders), keep_trailing_newline=True)
    tmpl = env.get_template(template)
    return tmpl.render(**kwargs)


def prompt(template: str, **kwargs):
    """Render a prompt template (string) and return an object with to_llm()."""
    # If template is a callable style string (fn:... handled elsewhere), just render
    t = Template(template)
    rendered = t.render(**kwargs)

    class _PromptResult:
        def __init__(self, text):
            self._text = text

        def to_llm(self):
            return self._text

        def __str__(self):
            return self._text
        def __contains__(self, item):
            return item in self._text
        def __repr__(self):
            return self._text

    return _PromptResult(rendered)


class _Tokenizing:
    @staticmethod
    def num_tokens_from_string(s: str) -> int:
        if s is None:
            return 0
        # naive token count: words
        try:
            return len(str(s).split())
        except Exception:
            return len(str(s))

    @staticmethod
    def fit_to_token_size(lines, max_tokens: int):
        # lines may be list of strings or iterable of objects convertible to str
        if max_tokens is None:
            return list(lines), 0
        strs = [str(l) for l in lines]
        out = []
        total = 0
        for s in strs:
            tokens = _Tokenizing.num_tokens_from_string(s)
            if total + tokens > max_tokens:
                break
            out.append(s)
            total += tokens
        removed = max(0, len(strs) - len(out))
        return out, removed


tokenizing = _Tokenizing()


# Minimal Embedding DB enum expected by bootstrap
class EmbeddingDbType(Enum):
    NONE = 0


# Minimal logging config shim
class _LoggingConfig:
    # default value used by bootstrap; tests may modify this
    STRIP_REQUEST_LINES = [300, 15]


logging = type("logging", (), {"LoggingConfig": _LoggingConfig})


# Minimal exceptions used by bootstrap
class LLMConfigError(Exception):
    pass
