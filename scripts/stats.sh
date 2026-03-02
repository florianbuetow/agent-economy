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
row "Specs (md)"       "$(loc -name '*.md')"

printf "  %-20s ────────\n" ""
printf "  \033[1;37m%-20s\033[0m \033[1;33m%'8d\033[0m\n" "Total" "$total"

# ── Test counts per service ──────────────────────────────────────────────
count_tests() {
    if [ -d "$1" ]; then
        { grep -r -c -h 'def test_\|async def test_' "$1" 2>/dev/null || true; } \
            | awk '{s+=$1} END {print s+0}'
    else
        echo 0
    fi
}

dim="\033[0;90m"
white="\033[0;37m"
bold="\033[1;37m"
yellow="\033[1;33m"
reset="\033[0m"

printf "\n\033[1;34m  Test Functions (grep)\033[0m\n\n"

# Print a number as exactly 4 characters, right-aligned with leading spaces.
# Zero renders as a dim dot.
fmt4() {
    if [ "$1" -eq 0 ]; then
        printf "${dim}   ·${reset}"
    else
        printf "%4d" "$1"
    fi
}

printf "  ${bold}%-16s  %4s %5s %4s %4s %4s  %5s${reset}\n" \
    "" "unit" "integ" "e2e" "arch" "perf" "total"
printf "  %-16s  %4s %5s %4s %4s %4s  %5s\n" \
    "" "────" "─────" "────" "────" "────" "─────"

tot_unit=0 tot_integ=0 tot_e2e=0 tot_arch=0 tot_perf=0 grand=0

for svc_dir in services/*/; do
    [ -d "$svc_dir/tests" ] || continue
    svc=$(basename "$svc_dir")

    n_unit=$(count_tests "$svc_dir/tests/unit")
    n_integ=$(count_tests "$svc_dir/tests/integration")
    n_e2e=$(count_tests "$svc_dir/tests/e2e")
    n_arch=$(count_tests "$svc_dir/tests/architecture")
    n_perf=$(count_tests "$svc_dir/tests/performance")
    n_total=$((n_unit + n_integ + n_e2e + n_arch + n_perf))

    tot_unit=$((tot_unit + n_unit))
    tot_integ=$((tot_integ + n_integ))
    tot_e2e=$((tot_e2e + n_e2e))
    tot_arch=$((tot_arch + n_arch))
    tot_perf=$((tot_perf + n_perf))
    grand=$((grand + n_total))

    printf "  ${white}%-16s${reset}  " "$svc"
    fmt4 $n_unit; printf "  "; fmt4 $n_integ; printf " "; fmt4 $n_e2e; printf " "; fmt4 $n_arch; printf " "; fmt4 $n_perf
    printf "   ${bold}"; fmt4 $n_total; printf "${reset}\n"
done

printf "  %-16s  %4s %5s %4s %4s %4s  %5s\n" \
    "" "────" "─────" "────" "────" "────" "─────"

printf "  ${bold}%-16s${reset}  " "Total"
printf "${yellow}"; fmt4 $tot_unit; printf "${reset}  "
printf "${yellow}"; fmt4 $tot_integ; printf "${reset} "
printf "${yellow}"; fmt4 $tot_e2e; printf "${reset} "
printf "${yellow}"; fmt4 $tot_arch; printf "${reset} "
printf "${yellow}"; fmt4 $tot_perf
printf "${reset}   ${yellow}"; fmt4 $grand; printf "${reset}\n"
printf "\n"
