#!/usr/bin/env bash
# init_overlay.sh
# ------------------------------------------------------------
# Creates a new Kustomize overlay from the set of *.yaml.template
# files found under overlays/template.
#
#   1. Prompts for a namespace, a hostname and an overlay name.
#   2. Builds the SECRET_NAME variable from the hostname.
#   3. Runs each *.yaml.template through `envsubst` and writes the
#      result (without the .template suffix) into the new overlay
#      directory.
# ------------------------------------------------------------

set -euo pipefail               # abort on errors, unset vars, etc.

# ---------- Helper functions ----------
die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

# ---------- Gather user input ----------
read -rp "Enter the namespace: " NAMESPACE
NAMESPACE="${NAMESPACE#"${NAMESPACE%%[![:space:]]*}"}"
NAMESPACE="${NAMESPACE%"${NAMESPACE##*[![:space:]]}"}"

read -rp "Enter the hostname (e.g. app.example.com): " NEW_HOST
read -rp "Enter a name for the overlay directory (default: $NAMESPACE): " OVERLAYNAME
read -rp "Enter S3 backup bucket name (default: dspace-backups): " S3_BACKUP_BUCKET
read -rp "Enter S3 backup endpoint URL (default: https://s3.cl4.du.cesnet.cz): " S3_BACKUP_ENDPOINT

# Trim possible surrounding whitespace
NEW_HOST="${NEW_HOST#"${NEW_HOST%%[![:space:]]*}"}"
NEW_HOST="${NEW_HOST%"${NEW_HOST##*[![:space:]]}"}"
OVERLAYNAME="${OVERLAYNAME#"${OVERLAYNAME%%[![:space:]]*}"}"
OVERLAYNAME="${OVERLAYNAME%"${OVERLAYNAME##*[![:space:]]}"}"
S3_BACKUP_BUCKET="${S3_BACKUP_BUCKET#"${S3_BACKUP_BUCKET%%[![:space:]]*}"}"
S3_BACKUP_BUCKET="${S3_BACKUP_BUCKET%"${S3_BACKUP_BUCKET##*[![:space:]]}"}"
S3_BACKUP_ENDPOINT="${S3_BACKUP_ENDPOINT#"${S3_BACKUP_ENDPOINT%%[![:space:]]*}"}"
S3_BACKUP_ENDPOINT="${S3_BACKUP_ENDPOINT%"${S3_BACKUP_ENDPOINT##*[![:space:]]}"}"

# Set defaults if empty
[[ -z "$S3_BACKUP_BUCKET" ]] && S3_BACKUP_BUCKET="dspace-backups"
[[ -z "$S3_BACKUP_ENDPOINT" ]] && S3_BACKUP_ENDPOINT="https://s3.cl4.du.cesnet.cz"
[[ -z "$OVERLAYNAME" ]] && OVERLAYNAME="$NAMESPACE"

# Validate inputs
[[ -z "$NAMESPACE" ]] && die "Namespace cannot be empty."
[[ -z "$NEW_HOST" ]] && die "Hostname cannot be empty."
[[ -z "$OVERLAYNAME" ]] && die "Overlay name cannot be empty."

# ---------- Build derived variable ----------
# Replace every '.' with '-' and add the '-tls' suffix.
# Example:  www.example.com  ->  www-example-com-tls
SECRET_NAME="${NEW_HOST//./-}-tls"

# Export variables so that envsubst sees them.
export NAMESPACE NEW_HOST SECRET_NAME S3_BACKUP_BUCKET S3_BACKUP_ENDPOINT

# ---------- Paths ----------
TEMPLATE_DIR="overlays/template"
TARGET_DIR="overlays/${OVERLAYNAME}"

# Check that the template directory exists
[[ -d "$TEMPLATE_DIR" ]] || die "Template directory '$TEMPLATE_DIR' does not exist."

# Create (or clean) the target overlay directory
if [[ -d "$TARGET_DIR" ]]; then
  echo "Overlay directory '$TARGET_DIR' already exists – its contents will be overwritten."
  read -rp "Continue (y/n): " response
  if [[ "$response" =~ ^[Yy]$ ]]; then
        :
  else
      echo "Action cancelled."
      exit 0
  fi
else
  mkdir -p "$TARGET_DIR"
fi

# ---------- Process each template ----------
shopt -s nullglob             # make sure the loop runs 0 times if no matches
for tmpl in "$TEMPLATE_DIR"/*.yaml.template; do
  # Strip the directory and the .template suffix
  base_name="$(basename "$tmpl" .template)"   # e.g. deployment.yaml
  out_file="${TARGET_DIR}/${base_name}"

  # Run envsubst and write the result
  envsubst < "$tmpl" > "$out_file"

  echo "Generated ${out_file}"
done

echo "Overlay '${OVERLAYNAME}' created successfully in '${TARGET_DIR}'."
exit 0
