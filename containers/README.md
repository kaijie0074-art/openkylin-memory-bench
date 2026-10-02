# Native-agent images

These Debian development images **are not an openKylin validation environment**.
Runtime uses `--pull=never` and records the inspected image digest. The current
observed tags/digests and CLI probe records are in [agent-images.json](../docs/agent-images.json),
not inferred from the build recipes below.

Build only through an explicit command from the project root. Choose an unused tag
and check that it does not already exist; never overwrite a tag referenced by a run.
For example, these recipes create timestamped rebuild tags:

```sh
kmb_build_stamp=$(date -u +%Y%m%dT%H%M%SZ)
docker --context colima-kmb build -f containers/openclaw.Dockerfile -t "kmb-openclaw:2026.9.6-rebuild-${kmb_build_stamp}" .
docker --context colima-kmb build -f containers/hermes.Dockerfile -t "kmb-hermes:2ffa4977baf9-rebuild-${kmb_build_stamp}" .
```

A new build does not become the configured runtime image automatically. Record its
digest, source/worker hash, dependencies and CLI/config probe results before selecting
it. The Hermes `settle-v1` worker snapshot predates the later OpenClaw terminal patch;
its Hermes branch is unchanged, but the whole worker file is not the current source.
See the image record for any subsequent rebuild instead of replacing historical metadata.

Upstream dependency resolution is not fully locked, so rebuilding a tag is not proof
of byte-identical content. Hermes uses upstream's supported editable installation
(`pip install -e /opt/hermes`); its packaging guard rejects ordinary wheel/sdist
installation. Apt, source checkout and Python installation use separate cache layers.

Recipes alone imply no installation success, model compatibility or benchmark result.
The dynamic CLI probe checks flags and native configuration in the actual image.
Do not mount repository, rubric, reports, Docker socket, host home or personal profiles.
Only a newly created `.runtime/subject-*` directory is mounted at `/trial`.

The trusted gateway remains on the host. Pass its **trial token**, never the real
provider key, to the adapter. `host.docker.internal` must reach that gateway from the
selected Docker context. The gateway records model identity, call evidence and the
tool-proposal budget; native tool reports remain subject claims. Container bridge
networking is not a network egress allowlist. See [adapter details](../docs/agent-adapters.md).
