#!/bin/bash
# ZONE_EDITOR_TEST.sh — Quick test script for zone editor implementation

echo "=== ZONE EDITOR IMPLEMENTATION TEST ==="
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

test_count=0
pass_count=0

test_file() {
  local file=$1
  local type=$2
  test_count=$((test_count + 1))
  
  if [ ! -f "$file" ]; then
    echo -e "${RED}✗ FAIL${NC}: $file not found"
    return 1
  fi
  
  echo -e "${GREEN}✓ PASS${NC}: $file exists"
  pass_count=$((pass_count + 1))
  return 0
}

echo "1. FILE CREATION TESTS"
echo "====================="
test_file "js/zone_editor.js" "javascript"
test_file "backend/zone_handler.py" "python"
test_file "ZONE_EDITOR_README.md" "documentation"

echo ""
echo "2. PYTHON SYNTAX TESTS"
echo "====================="
test_count=$((test_count + 1))
if python3 -m py_compile backend/zone_handler.py 2>/dev/null; then
  echo -e "${GREEN}✓ PASS${NC}: backend/zone_handler.py syntax OK"
  pass_count=$((pass_count + 1))
else
  echo -e "${RED}✗ FAIL${NC}: backend/zone_handler.py has syntax errors"
fi

test_count=$((test_count + 1))
if python3 -m py_compile backend/server.py 2>/dev/null; then
  echo -e "${GREEN}✓ PASS${NC}: backend/server.py syntax OK"
  pass_count=$((pass_count + 1))
else
  echo -e "${RED}✗ FAIL${NC}: backend/server.py has syntax errors"
fi

echo ""
echo "3. JAVASCRIPT SYNTAX TESTS"
echo "=========================="
test_count=$((test_count + 1))
if node -c js/zone_editor.js 2>/dev/null; then
  echo -e "${GREEN}✓ PASS${NC}: js/zone_editor.js syntax OK"
  pass_count=$((pass_count + 1))
else
  echo -e "${RED}✗ FAIL${NC}: js/zone_editor.js has syntax errors"
fi

echo ""
echo "4. DEPENDENCY TESTS"
echo "==================="
test_count=$((test_count + 1))
if python3 -c "import PIL" 2>/dev/null; then
  echo -e "${GREEN}✓ PASS${NC}: PIL/Pillow available"
  pass_count=$((pass_count + 1))
else
  echo -e "${YELLOW}⚠ WARN${NC}: PIL/Pillow not available (masks won't be generated)"
fi

test_count=$((test_count + 1))
if python3 -c "import yaml" 2>/dev/null; then
  echo -e "${GREEN}✓ PASS${NC}: PyYAML available"
  pass_count=$((pass_count + 1))
else
  echo -e "${YELLOW}⚠ WARN${NC}: PyYAML not available (YAML handling disabled)"
fi

test_count=$((test_count + 1))
if python3 -c "import flask" 2>/dev/null; then
  echo -e "${GREEN}✓ PASS${NC}: Flask available"
  pass_count=$((pass_count + 1))
else
  echo -e "${RED}✗ FAIL${NC}: Flask not available"
fi

echo ""
echo "5. MAP FILES TEST"
echo "================"
test_count=$((test_count + 1))
map_count=$(ls maps/*.pgm 2>/dev/null | wc -l)
if [ "$map_count" -gt 0 ]; then
  echo -e "${GREEN}✓ PASS${NC}: Found $map_count maps"
  pass_count=$((pass_count + 1))
  ls maps/*.pgm | sed 's|maps/||; s|\.pgm||' | sed 's/^/  - /'
else
  echo -e "${RED}✗ FAIL${NC}: No maps found in maps/ directory"
fi

echo ""
echo "6. ZONE FILE STRUCTURE"
echo "======================"
test_count=$((test_count + 1))
if [ -w "maps/" ]; then
  echo -e "${GREEN}✓ PASS${NC}: maps/ directory is writable"
  pass_count=$((pass_count + 1))
else
  echo -e "${RED}✗ FAIL${NC}: maps/ directory is not writable"
fi

echo ""
echo "=== SUMMARY ==="
echo "Tests passed: $pass_count / $test_count"
echo ""

if [ "$pass_count" -eq "$test_count" ]; then
  echo -e "${GREEN}✓ ALL TESTS PASSED!${NC}"
  echo ""
  echo "Zone Editor is ready to use. Start the Flask server:"
  echo "  python3 backend/server.py"
  echo ""
  echo "Then navigate to the Localization view and click 'OPEN ZONE EDITOR'"
  exit 0
else
  echo -e "${YELLOW}⚠ Some tests failed or warnings present${NC}"
  exit 1
fi
