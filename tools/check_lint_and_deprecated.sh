#!/usr/bin/bash
#
#  ####   #####     ##    #####      #     ####
# #    #  #    #   #  #   #    #     #    #    #
# #    #  #    #  #    #  #    #     #    #    #
# #    #  #####   ######  #    #     #    #    #
# #    #  #   #   #    #  #    #     #    #    #
#  ####   #    #  #    #  #####      #     ####
#
# Created on July 2, 2026
# @author:		 Henk Stevens & Olaf Mastenbroek & Onno Janssen
# @copyright:	 Stichting Oradio
# @license:		 GNU General Public License (GPL)
# @organization: Stichting Oradio
# @version:	   	 1
# @email:		 info@stichtingoradio.nl
# @status:		 Development
# @purpose:		 Check code quality
#		Run pylint, ruff, mypy and custom runtime deprecation checker.

# Color definitions
RED='\033[1;31m'
YELLOW='\033[1;93m'
GREEN='\033[1;32m'
NC='\033[0m'

# Require bash — this script uses bash-specific constructs
if [ -z "${BASH:-}" ]; then
	echo "${RED}This script requires bash${NC}"
	exit 1
fi

##### Initialize ###############################

BASE="$HOME/Oradio3"

FILES=("$@")

# fallback: no arguments → default glob
if [ ${#FILES[@]} -eq 0 ]; then
    FILES=($BASE/Main/*.py $BASE/module_test/*.py)
fi

# Create empty log file capturing rclone output
RESULT_FILE="lint_deprecated_result.txt"
: > "$RESULT_FILE"	# truncate/create log

if systemctl is-active --quiet "oradio"; then
	if sudo systemctl stop "oradio" >/dev/null 2>&1; then
		echo -e "${YELLOW}oradio.service stopped.${NC}"
	else
		echo -e "${RED}Failed to stop oradio.service${NC}"
		exit 1
	fi
fi

##### Dependencies #############################

PYTHON_PACKAGES=(
	pylint
	ruff
	mypy
)

# Ensure Python packages are installed and up-to-date
for package in "${PYTHON_PACKAGES[@]}"; do
	installed_version=$(python -c "
import sys
try:
    from importlib.metadata import version
except ImportError:
    from importlib_metadata import version  # For Python < 3.8 with backport
try:
    print(version('$package'))
except:
    sys.exit(1)
")
	if [ $? -ne 0 ]; then
		echo -e "${YELLOW}$package is missing: installing...${NC}"
		# On --use-pep517 see https://peps.python.org/pep-0517/
		python3 -m pip install --upgrade --use-pep517 $package
		continue
	fi

	# Get latest version from PyPI
	latest_version=$(curl -s "https://pypi.org/pypi/${package}/json" | \
		python -c "import sys, json; print(json.load(sys.stdin)['info']['version'])" 2>/dev/null)

	if [ -z "$latest_version" ]; then
		echo -e "${ERROR} Failed to fetch version for $package from PyPI${NC}"
		exit 1
	fi

	# Compare versions
	if [ "$installed_version" != "$latest_version" ]; then
		echo -e "${YELLOW}$package is outdated: upgrading...${NC}"
		# On --use-pep517 see https://peps.python.org/pep-0517/
		python3 -m pip install --upgrade --use-pep517 $package
	else
		echo "$package is up-to-date"
	fi
done

echo "$(date +'%Y-%m-%d %H:%M:%S'): Start lint and deprecation check" | tee -a "$RESULT_FILE"
echo >> "$RESULT_FILE"

##### Pylint #####################################

echo "### 🧹 Pylint analysis" | tee -a "$RESULT_FILE"
for file in "${FILES[@]}"; do
    echo "Checking $file with pylint..."
    pylint "$file" \
        --rcfile=$BASE/.github/workflows/.pylintrc \
        --output-format=text \
		--score=n >>"$RESULT_FILE" 2>&1
done
count_pylint=$(grep -Ec '^[^:]+:[0-9]+:[0-9]+: [A-Z][0-9]{4}:' "$RESULT_FILE" || true)
if [ "$count_pylint" -eq 0 ]; then
	echo "Pylint found no issues." >> "$RESULT_FILE"
fi
echo >> "$RESULT_FILE"

##### Ruff #######################################

echo "### 🧹 Ruff static deprecation analysis" | tee -a "$RESULT_FILE"
for file in "${FILES[@]}"; do
    echo "Checking $file with ruff..."
    ruff check "$file" \
        --select UP \
        --output-format=concise \
		--quiet	>>"$RESULT_FILE" 2>&1
done
count_ruff=$(grep -Ec '^[^:]+:[0-9]+:[0-9]+: UP[0-9]{3,4}' "$RESULT_FILE" || true)
if [ "$count_ruff" -eq 0 ]; then
	echo "Ruff found no issues." >> "$RESULT_FILE"
fi
echo >> "$RESULT_FILE"

##### Mypy #######################################

echo "### 🧹 Mypy type-check" | tee -a "$RESULT_FILE"
for file in "${FILES[@]}"; do
    echo "Checking $file with mypy..."
	mypy "$file" \
		--ignore-missing-imports \
		--enable-error-code=deprecated --report-deprecated-as-note \
		--no-error-summary >> "$RESULT_FILE" 2>&1
done
count_mypy=$(grep -c ': error:' "$RESULT_FILE" || true)
if [ "$count_mypy" -eq 0 ]; then
	echo "Mypy found no errors." >> "$RESULT_FILE"
fi
echo >> "$RESULT_FILE"

##### Runtime ####################################

echo "### 🧹 Runtime deprecation analysis" | tee -a "$RESULT_FILE"
for file in "${FILES[@]}"; do
	echo "Checking $file deprecation runtime..."
	python3 $BASE/module_test/deprecation_check.py $file >> "$RESULT_FILE" 2>&1
done
count_runtime=$(grep -Ec '^DEPRECATION \(' "$RESULT_FILE" || true)
if [ "$count_runtime" -eq 0 ]; then
	echo "Runtime check found no deprecation warnings." >> "$RESULT_FILE"
fi

##### Report #####################################

# Options: "✅ Pass", "⚠️ Warn", "❌ Fail"
# -----------------------------------------------------------------
pylint_status=$( [ "$count_pylint"  -eq 0 ] && echo "✅ Pass" || echo "❌ Fail")
ruff_status=$(   [ "$count_ruff"    -eq 0 ] && echo "✅ Pass" || echo "❌ Fail")
mypy_status=$(   [ "$count_mypy"    -eq 0 ] && echo "✅ Pass" || echo "❌ Fail")
runtime_status=$([ "$count_runtime" -eq 0 ] && echo "✅ Pass" || echo "❌ Fail")

# Fix string length for nice table rendering
printf -v count_pylint  " %-6s" "$count_pylint"
printf -v count_ruff    " %-6s" "$count_ruff"
printf -v count_mypy    " %-6s" "$count_mypy"
printf -v count_runtime " %-6s" "$count_runtime"

cat <<EOF
### 🧹 Python Lint & Deprecation Summary
| Check                    | #Issues | Status  |
|--------------------------|---------|---------|
| Pylint (lint)            | $count_pylint | $pylint_status |
| Ruff UP (static deprec.) | $count_ruff | $ruff_status |
| Mypy (type errors)       | $count_mypy | $mypy_status |
| Runtime deprecations     | $count_runtime | $runtime_status |
EOF

echo >> "$RESULT_FILE"
echo -e "$(date +'%Y-%m-%d %H:%M:%S'): Finished lint and deprecation check" | tee -a "$RESULT_FILE"
