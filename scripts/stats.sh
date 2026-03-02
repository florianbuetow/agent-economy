#!/usr/bin/env bash
# Show lines of code across the project.
# Called from: just stats

set -euo pipefail

loc() {
    find . \
        -path './.git' -prune -o \
        -path '*/.venv' -prune -o \
        -path '*/node_modules' -prune -o \
        -path './.claude' -prune -o \
        "$@" -type f -print 2>/dev/null \
        | xargs cat 2>/dev/null | wc -l | tr -d ' '
}

total=0
row() {
    local label=$1
    local lines=$2
    total=$((total + lines))
    printf "  \033[0;37m%-20s\033[0m %'8d\n" "$label" "$lines"
}

py_src=$(find . \
    -path './.git' -prune -o \
    -path '*/.venv' -prune -o \
    -path '*/node_modules' -prune -o \
    -path './.claude' -prune -o \
    -name '*.py' -path '*/src/*' -type f -print 2>/dev/null \
    | xargs cat 2>/dev/null | wc -l | tr -d ' ')

py_test=$(find . \
    -path './.git' -prune -o \
    -path '*/.venv' -prune -o \
    -path '*/node_modules' -prune -o \
    -path './.claude' -prune -o \
    -name '*.py' -path '*/tests/*' -type f -print 2>/dev/null \
    | xargs cat 2>/dev/null | wc -l | tr -d ' ')

printf "\n"
printf "\033[1;34m  Lines of Code\033[0m\n\n"

row "Python (source)"  "$py_src"
row "Python (tests)"   "$py_test"
row "TypeScript"       "$(loc -name '*.ts' -o -name '*.tsx')"
row "Shell"            "$(loc -name '*.sh')"
row "Justfiles"        "$(loc -name 'justfile')"
row "YAML"             "$(loc -name '*.yaml' -o -name '*.yml')"
row "TOML"             "$(loc -name '*.toml')"
row "Docker"           "$(loc -name 'Dockerfile*' -o -name 'docker-compose*')"
row "Markdown"         "$(loc -name '*.md')"

printf "  %-20s ────────\n" ""
printf "  \033[1;37m%-20s\033[0m \033[1;33m%'8d\033[0m\n" "Total" "$total"
printf "\n"
