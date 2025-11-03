#!/usr/bin/bash
#
# make_encrypted_conf.sh
# ------------------------------------------------------------------------------
# Utility to create and verify the encrypted rclone config block used in
# the Oradio USB installer.
#
# Usage:
#   ./make_encrypted_conf.sh create    # Encrypt ~/.config/rclone/rclone.conf
#   ./make_encrypted_conf.sh verify    # Decrypt sharepoint.conf.enc and verify
#
# Output:
#   - Creates sharepoint.conf.enc (base64 AES-256-CBC + PBKDF2)
#   - Safe to paste directly between BEGIN/END markers in installer
#
# Author: Stichting Oradio
# Version: 1.2
# Date: 2025-11-03
# ------------------------------------------------------------------------------

set -e

SRC="/home/pi/.config/rclone/rclone.conf"
PLAIN="sharepoint.conf"
ENC="sharepoint.conf.enc"
TMP_DEC="/tmp/sharepoint_test.conf"

create_block() {
  echo "=== Creating encrypted rclone config block ==="

  if [ ! -f "$SRC" ]; then
    echo "❌  Source rclone.conf not found at: $SRC"
    echo "    Make sure rclone is configured for the pi user."
    exit 1
  fi

  cp "$SRC" "$PLAIN"
  echo
  echo "🔐  Encrypting ${PLAIN} → ${ENC}"
  echo "    You will be prompted for a password. Use a strong one."
  echo
  openssl enc -aes-256-cbc -pbkdf2 -salt -in "$PLAIN" -out "$ENC" -base64
  echo
  echo "✅  Encrypted block created: $ENC"
  echo "   Paste its full content (including trailing ==) into the installer."
  echo

  read -p "Remove plaintext copy (${PLAIN})? [y/N]: " ans
  if [[ "$ans" =~ ^[Yy]$ ]]; then
    shred -u "$PLAIN" 2>/dev/null || rm -f "$PLAIN"
    echo "🧹  Plaintext removed."
  else
    echo "⚠️  Remember to delete ${PLAIN} manually after verifying."
  fi
}

verify_block() {
  echo "=== Verifying existing encryption block ==="
  if [ ! -f "$ENC" ]; then
    echo "❌  Encrypted file not found: $ENC"
    exit 1
  fi

  echo "🔓  Enter the password used during encryption:"
  if openssl enc -d -aes-256-cbc -pbkdf2 -base64 -in "$ENC" -out "$TMP_DEC"; then
    echo "✅  Decryption successful."
    echo "   Temporary output at: $TMP_DEC"
    echo
    head -n 10 "$TMP_DEC"
    echo "..."
    echo "⚠️  Remove decrypted file after inspection:"
    echo "      rm -f $TMP_DEC"
  else
    echo "❌  Decryption failed (wrong password or corrupt file)."
    rm -f "$TMP_DEC" 2>/dev/null || true
    exit 1
  fi
}

case "$1" in
  create) create_block ;;
  verify) verify_block ;;
  *)
    echo "Usage:"
    echo "  $0 create   # Encrypt ~/.config/rclone/rclone.conf"
    echo "  $0 verify   # Decrypt sharepoint.conf.enc for testing"
    exit 1 ;;
esac
