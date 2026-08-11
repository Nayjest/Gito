# Contributing

🌟 Contributions are welcome and will be fully credited! 🌟

## Workflow

To contribute to this project, please note the following steps:

- Fork the repository on GitHub.
- Clone the forked repository to your machine.
- Make the necessary changes in your branch.
- Push your changes to your forked repository.
- Submit a pull request with a description of the changes.

More information can be found in the [GitHub documentation](https://docs.github.com/en/get-started/quickstart/contributing-to-projects).

## Tips
#### Enabling GitHub Workflows in your Fork
If you'd like to validate your changes privately on your own fork before opening a PR to Nayjest/Gito, you can.
GitHub disables Actions on new forks by default, so you'll need to turn them on first:

1. Go to the **Actions** tab of your forked repository and click the button to enable workflows.

   <img width="560" alt="Enabling workflows in the Actions tab of a fork" src="https://github.com/user-attachments/assets/d37eac21-4aaf-4013-b24f-be5f8ec5a063" />

2. Push your feature branch to your fork, or open an internal pull request from your feature branch to your fork's `main` branch.
3. Watch the test results on your fork's Actions or pull request page, and iterate until everything is green.

This lets you catch failures early and keep your upstream PR clean.

## Guidelines

- Please ensure to respect the existing style in the codebase and to include tests that cover your changes.

- Document any change in behaviour. Make sure the `README.md` and any other relevant documentation are kept up-to-date.

- One pull request per feature. If you want to do more than one thing, send multiple pull requests.

- Send coherent history. Make sure each individual commit in your pull request is meaningful. If you had to make multiple intermediate commits while developing, please squash them before submitting.

- Don't be alarmed if the "review" check on your fork PR fails or stays pending — it requires maintainer approval and doesn't reflect your code.


Run code formatter:
```bash
black .
```

Run code style checks:
```bash
flake8 .
pylint .
```

Run tests:
```bash
pytest
```

### Tests must not call a live LLM

All inference goes through microcore (`mc.llm`, `mc.allm`, `mc.llm_parallel`,
`mc.prompt(...).to_llm()`) — mock at that level, and remember the code path may
hit it more than once (`review()` runs per-file prompts *and* a summary prompt).

A missed mock is invisible locally: `~/.gito/.env` makes the test pass on a real
API call, then it fails in CI. Check against an empty environment:

```bash
HOME=/tmp/empty USERPROFILE=/tmp/empty LLM_API_KEY= LLM_API_TYPE= pytest
```

🚀 **Happy coding**!
