# Troubleshooting

## 1. LLM API Rate Limit / "Overloaded" Errors

You may decrease parallelization by setting the `MAX_CONCURRENT_TASKS` environment variable in the GitHub workflow files or in the local environment.

See [microcore configuration options](https://ai-microcore.github.io/api-reference/microcore/configuration.html#Config.MAX_CONCURRENT_TASKS) for more details.