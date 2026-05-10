"""Preapproved hosts for WebFetchTool.

These hosts skip the user-confirmation permission prompt and are
auto-allowed. Ported from Claude Code's ``preapproved.ts``.

Only covers code/developer documentation domains — sites that a
coding assistant would routinely need to read.
"""

from __future__ import annotations

PREAPPROVED_HOSTS: frozenset[str] = frozenset(
    {
        # Python
        "docs.python.org",
        "pypi.org",
        "peps.python.org",
        "packaging.python.org",
        "docs.pydantic.dev",
        "fastapi.tiangolo.com",
        "flask.palletsprojects.com",
        "docs.djangoproject.com",
        "docs.sqlalchemy.org",
        "www.sqlalchemy.org",
        "click.palletsprojects.com",
        "jinja.palletsprojects.com",
        "requests.readthedocs.io",
        "urllib3.readthedocs.io",
        "docs.aiohttp.org",
        "www.starlette.io",
        "docs.celeryq.dev",
        "docs.pytest.org",
        "mypy.readthedocs.io",
        # JavaScript / TypeScript
        "nodejs.org",
        "docs.npmjs.com",
        "registry.npmjs.org",
        "developer.mozilla.org",
        "tc39.es",
        "www.typescriptlang.org",
        "react.dev",
        "nextjs.org",
        "vuejs.org",
        "angular.io",
        "expressjs.com",
        "bun.sh",
        "deno.land",
        "vitest.dev",
        "jestjs.io",
        # Rust
        "docs.rs",
        "doc.rust-lang.org",
        "crates.io",
        "rust-lang.github.io",
        # Go
        "pkg.go.dev",
        "go.dev",
        "golang.org",
        # Java / JVM
        "docs.oracle.com",
        "spring.io",
        "docs.spring.io",
        "maven.apache.org",
        "central.sonatype.com",
        # Ruby
        "ruby-doc.org",
        "rubygems.org",
        "guides.rubyonrails.org",
        "api.rubyonrails.org",
        # C / C++
        "en.cppreference.com",
        "www.cplusplus.com",
        # .NET / C#
        "learn.microsoft.com",
        "docs.microsoft.com",
        "www.nuget.org",
        # Databases
        "www.postgresql.org",
        "dev.mysql.com",
        "redis.io",
        "www.sqlite.org",
        "www.mongodb.com",
        "docs.mongodb.com",
        # Cloud providers
        "docs.aws.amazon.com",
        "cloud.google.com",
        "docs.github.com",
        "docs.gitlab.com",
        "docs.docker.com",
        "kubernetes.io",
        "helm.sh",
        "www.terraform.io",
        "developer.hashicorp.com",
        # Code hosting
        "github.com",
        "raw.githubusercontent.com",
        "gist.github.com",
        "gitlab.com",
        "bitbucket.org",
        # General development
        "stackoverflow.com",
        "stackexchange.com",
        "en.wikipedia.org",
        "www.w3.org",
        "datatracker.ietf.org",
        "tools.ietf.org",
        "json-schema.org",
        "www.json.org",
        "yaml.org",
        "toml.io",
        "graphql.org",
        "grpc.io",
        "protobuf.dev",
        "swagger.io",
        "spec.openapis.org",
        # AI / ML
        "docs.anthropic.com",
        "platform.openai.com",
        "huggingface.co",
        "pytorch.org",
        "www.tensorflow.org",
        "scikit-learn.org",
        "numpy.org",
        "pandas.pydata.org",
        "matplotlib.org",
        # Observability
        "opentelemetry.io",
        "prometheus.io",
        "grafana.com",
        "docs.datadoghq.com",
        "docs.sentry.io",
    }
)

__all__ = ["PREAPPROVED_HOSTS"]
