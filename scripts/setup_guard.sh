if [[ ! -f .guardfile ]]; then
  guard init 755 root wheel
fi

# =============================================================================
# Collections — grouped by purpose, not file extension
# =============================================================================

guard create project-root
guard create ci-config
guard create app-config
guard create docker
guard create justfiles
guard create dependencies
guard create docs

# Per-service collections: source, per-test-type, and all-tests aggregate
for svc in identity central-bank task-board reputation court; do
  guard create "${svc}-service"
  guard create "${svc}-tests"
  for test_type in unit integration performance acceptance; do
    if [[ -d "./services/${svc}/tests/${test_type}" ]]; then
      guard create "${svc}-tests-${test_type}"
    fi
  done
done

# Add individual files
guard add file .guardfile

# =============================================================================
# project-root — repo-level meta files
# =============================================================================

guard update project-root add ./.gitignore
guard update project-root add ./AGENTS.md
guard update project-root add ./DELEGATE.md

# =============================================================================
# ci-config — linting, static analysis, type checking, quality tools
# =============================================================================

guard update ci-config add ./.pre-commit-config.yaml
guard update ci-config add ./.semgrepignore
guard update ci-config add ./config/codespell/ignore.txt
guard update ci-config add ./config/semgrep/no-default-values.yml
guard update ci-config add ./config/semgrep/no-noqa.yml
guard update ci-config add ./config/semgrep/no-type-suppression.yml

fd --type f pyrightconfig.json ./services/ -0 | \
while IFS= read -r -d '' file; do \
  guard update ci-config add "$file"
done

# =============================================================================
# app-config — service runtime configuration (config.yaml per service)
# =============================================================================

for subdir in ./services/*/; do
  config_file="${subdir}config.yaml"
  if [[ -f "$config_file" ]]; then
    guard update app-config add "$config_file"
  fi
done

# =============================================================================
# docker — Dockerfiles and docker-compose orchestration
# =============================================================================

guard update docker add ./docker-compose.yml
guard update docker add ./docker-compose.dev.yml

fd --type f Dockerfile ./services/ -0 | \
while IFS= read -r -d '' file; do \
  guard update docker add "$file"
done

# =============================================================================
# justfiles — build system recipes
# =============================================================================

fd --type f justfile . -0 | \
while IFS= read -r -d '' file; do \
  guard update justfiles add "$file"
done

# =============================================================================
# dependencies — Python dependency declarations
# =============================================================================

fd --type f pyproject.toml . -0 | \
while IFS= read -r -d '' file; do \
  guard update dependencies add "$file"
done

# =============================================================================
# docs — all documentation
# =============================================================================

fd --type f --extension md . ./docs/ -0 | \
while IFS= read -r -d '' file; do \
  guard update docs add "$file"
done

# =============================================================================
# Per-service: source code + test collections
# =============================================================================

for svc in identity central-bank task-board reputation court; do
  svc_dir="./services/${svc}"

  # Source code
  if [[ -d "${svc_dir}/src" ]]; then
    fd --type f . "${svc_dir}/src/" -0 | \
    while IFS= read -r -d '' file; do \
      guard update "${svc}-service" add "$file"
    done
  fi

  # Tests — per type and aggregate
  for test_type in unit integration performance acceptance; do
    target="${svc_dir}/tests/${test_type}/"
    if [[ -d "$target" ]]; then
      fd --type f . "$target" -0 | \
      while IFS= read -r -d '' file; do \
        guard update "${svc}-tests-${test_type}" add "$file"
        guard update "${svc}-tests" add "$file"
      done
    fi
  done
done
