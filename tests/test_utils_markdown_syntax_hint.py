from gito.utils.markdown import syntax_hint


def test_extensions():
    assert syntax_hint("main.py") == "python"
    assert syntax_hint("script.PY") == "python"
    assert syntax_hint("foo.test.py") == "python"
    assert syntax_hint("index.html") == "html"
    assert syntax_hint("style.scss") == "scss"
    assert syntax_hint("file.json") == "json"
    assert syntax_hint("readme.md") == "markdown"
    assert syntax_hint("rstfile.rst") == "rest"
    assert syntax_hint("folder/folder2/run.sh") == "bash"
    assert syntax_hint("build.mk") == "makefile"
    assert syntax_hint("Dockerfile") == "dockerfile"
    assert syntax_hint("main.ts") == "typescript"
    assert syntax_hint("main.java") == "java"
    assert syntax_hint("foo.go") == "go"
    assert syntax_hint("code.cpp") == "cpp"
    assert syntax_hint("folder.1\\file.hello.cxx") == "cpp"
    assert syntax_hint("CMakeLists.txt") == "cmake"


def test_unknown_extension():
    assert syntax_hint("thing.qqq") == "qqq"
    assert syntax_hint("noext") == ""
