# <a href="https://github.com/Nayjest/Gito"><img src="https://raw.githubusercontent.com/Nayjest/Gito/main/press-kit/logo/gito-bot-1_64top.png" align="left" width=64 height=50 title="Gito: AI Code Reviewer"></a>Troubleshooting

## 1. LLM API Rate Limit / "Overloaded" Errors

You may decrease parallelization by setting the `MAX_CONCURRENT_TASKS` environment variable in the GitHub workflow files or in the local environment.

See [microcore configuration options](https://ai-microcore.github.io/api-reference/microcore/configuration.html#Config.MAX_CONCURRENT_TASKS) for more details.