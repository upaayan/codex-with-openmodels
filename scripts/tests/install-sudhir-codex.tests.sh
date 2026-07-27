#!/bin/sh
set -eu

repo_root="$(
    CDPATH= cd -- "$(dirname "$0")/../.." &&
        pwd
)"
test_root="$(mktemp -d "${TMPDIR:-/tmp}/install-sudhir-codex-test.XXXXXX")"
trap 'rm -rf "${test_root}"' EXIT HUP INT TERM

fixture_root="${test_root}/fork"
fixture_home="${test_root}/home"
fake_bin="${test_root}/bin"
archive_stage="${test_root}/archive"
uv_log="${test_root}/uv.log"
archive="${test_root}/codex-with-openmodels-x86_64-unknown-linux-musl.tar.gz"
checksums="${test_root}/SHA256SUMS"

mkdir -p \
    "${fixture_root}/codex-rs" \
    "${fixture_root}/scripts" \
    "${fixture_root}/sudhir_codex/cursor_worker" \
    "${fixture_home}" \
    "${fake_bin}" \
    "${archive_stage}/codex-resources"

cp "${repo_root}/scripts/install-sudhir-codex" \
    "${fixture_root}/scripts/install-sudhir-codex"
printf '%s\n' '#!/bin/sh' 'exit 0' \
    >"${fixture_root}/scripts/sudhir-codex"
chmod +x "${fixture_root}/scripts/sudhir-codex"

for binary in \
    codex \
    codex-code-mode-host \
    codex-responses-api-proxy \
    codex-resources/bwrap; do
    printf '%s\n' '#!/bin/sh' 'exit 0' >"${archive_stage}/${binary}"
    chmod +x "${archive_stage}/${binary}"
done
tar -C "${archive_stage}" -czf "${archive}" \
    codex \
    codex-code-mode-host \
    codex-responses-api-proxy \
    codex-resources/bwrap
archive_hash="$(sha256sum "${archive}" | awk '{ print $1 }')"
printf '%s  %s\n' "${archive_hash}" "$(basename "${archive}")" >"${checksums}"

printf '%s\n' \
    '#!/bin/sh' \
    'case "${1:-}" in' \
    '    -s) printf "%s\n" Linux ;;' \
    '    -m) printf "%s\n" x86_64 ;;' \
    '    *) printf "%s\n" Linux ;;' \
    'esac' >"${fake_bin}/uname"
chmod +x "${fake_bin}/uname"

printf '%s\n' \
    '#!/bin/sh' \
    'set -eu' \
    'printf "%s\n" "$*" >>"${UV_LOG}"' \
    'if [ "${1:-}" = "venv" ]; then' \
    '    venv_path=""' \
    '    clear=0' \
    '    for argument in "$@"; do' \
    '        venv_path="${argument}"' \
    '        if [ "${argument}" = "--clear" ]; then' \
    '            clear=1' \
    '        fi' \
    '    done' \
    '    if [ -d "${venv_path}" ] && [ "${clear}" -ne 1 ]; then' \
    '        printf "%s\n" "existing virtual environment requires --clear" >&2' \
    '        exit 42' \
    '    fi' \
    '    if [ "${clear}" -eq 1 ]; then' \
    '        rm -rf "${venv_path}"' \
    '    fi' \
    '    mkdir -p "${venv_path}/bin"' \
    '    printf "%s\n" "#!/bin/sh" "exit 0" >"${venv_path}/bin/python"' \
    '    chmod +x "${venv_path}/bin/python"' \
    'fi' >"${fake_bin}/uv"
chmod +x "${fake_bin}/uv"

printf '%s\n' '#!/bin/sh' 'exit 0' >"${fake_bin}/npm"
chmod +x "${fake_bin}/npm"

HOME="${fixture_home}" \
PATH="${fake_bin}:${PATH}" \
UV_LOG="${uv_log}" \
SUDHIR_CODEX_ROOT="${fixture_root}" \
SUDHIR_CODEX_STATE="${fixture_home}/.sudhir-codex" \
SUDHIR_CODEX_PYTHON="/usr/local/bin/python3.12" \
SUDHIR_CODEX_UV="${fake_bin}/uv" \
SUDHIR_CODEX_NPM="${fake_bin}/npm" \
    "${fixture_root}/scripts/install-sudhir-codex" \
    --archive "${archive}" \
    --checksums "${checksums}"

HOME="${fixture_home}" \
PATH="${fake_bin}:${PATH}" \
UV_LOG="${uv_log}" \
SUDHIR_CODEX_ROOT="${fixture_root}" \
SUDHIR_CODEX_STATE="${fixture_home}/.sudhir-codex" \
SUDHIR_CODEX_PYTHON="/usr/local/bin/python3.12" \
SUDHIR_CODEX_UV="${fake_bin}/uv" \
SUDHIR_CODEX_NPM="${fake_bin}/npm" \
    "${fixture_root}/scripts/install-sudhir-codex" \
    --archive "${archive}" \
    --checksums "${checksums}"

grep -F \
    "venv --clear --python /usr/local/bin/python3.12 ${fixture_root}/.venv" \
    "${uv_log}" >/dev/null

printf '%s\n' "install-sudhir-codex tests passed"
