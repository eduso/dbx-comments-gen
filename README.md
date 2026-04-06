# dbx-comments-gen

Databricks project for automated comment generation.

## Project Structure

```
dbx-comments-gen/
├── src/
│   ├── notebooks/       # Databricks notebooks
│   ├── jobs/            # Job definitions and workflows
│   ├── pipelines/       # DLT and data pipelines
│   └── utils/           # Shared utilities and helpers
├── tests/
│   ├── unit/            # Unit tests
│   └── integration/     # Integration tests
├── config/              # Environment configs (dev, staging, prod)
├── data/
│   ├── raw/             # Raw input data (local dev)
│   └── processed/       # Processed output data (local dev)
├── infrastructure/
│   └── terraform/       # IaC for Databricks resources
└── .github/
    └── workflows/       # CI/CD pipelines
```

## Setup

1. Configure Databricks CLI authentication
2. Set environment variables in `config/`
3. Deploy infrastructure with Terraform
4. Run notebooks or jobs from `src/`

## Testing

```bash
pytest tests/
```
