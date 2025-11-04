import pytest
import tempfile
import os
from git import Repo


@pytest.fixture
def temp_git_repo_with_files():
    with tempfile.TemporaryDirectory() as temp_dir:
        repo = Repo.init(temp_dir)
        # Create .gitignore file
        gitignore_path = os.path.join(temp_dir, ".gitignore")
        repo.index.add(gitignore_path)
        repo.index.commit("Add three test files")
        with open(gitignore_path, "w") as gitignore_file:
            gitignore_file.write("*.tmp\n")
        file_names = ["test1.txt", "test2.txt", "test3.txt"]
        for i, file_name in enumerate(file_names, 1):
            file_path = os.path.join(temp_dir, file_name)
            with open(file_path, "w") as f:
                f.write(f"This is test file {i}\n")
        repo.index.add(file_names)
        repo.index.commit("Add three test files")
        yield temp_dir, repo
