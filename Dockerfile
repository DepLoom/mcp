FROM node:24.18.0-bookworm-slim AS node
FROM python:3.12-slim-bookworm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && useradd --create-home --uid 10001 deploom
WORKDIR /opt/deploom
COPY pyproject.toml setup.py build_support.py MANIFEST.in README.md release-manifest.json ./
COPY src ./src
COPY contracts ./contracts
COPY skills ./skills
COPY scripts ./scripts
COPY upstream/deploom ./upstream/deploom
RUN pip install --no-cache-dir .
USER deploom
WORKDIR /home/deploom
EXPOSE 8000
ENTRYPOINT ["python", "-m", "deploom_mcp", "--transport", "streamable-http", "--host", "0.0.0.0"]
