#!/usr/bin/env bash
# ── adjust these ───────────────────────────────────────────────────────────────
REGION="eu-north-1"                   # e.g. london; keep "us-east-1" if you prefer
BUCKET="meld-downloads"               # MUST be globally unique
LOCAL_MSI="/e/Coding_projects/build_windows_x64_vc17_Release/meld-4.4.2-git.6e5a2fb4ed9c-windows64.msi"
# ───────────────────────────────────────────────────────────────────────────────

# Create a temporary directory that works in Git Bash on Windows
TEMP_DIR="$(pwd)/temp_aws_policy"
mkdir -p "$TEMP_DIR"

echo "Creating/verifying S3 bucket..."
# 1  Create the bucket
if [ "$REGION" = "us-east-1" ]; then
  aws s3api create-bucket --bucket "$BUCKET" --region "$REGION"
else
  aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
    --create-bucket-configuration LocationConstraint="$REGION"
fi

# 2  Enforce bucket-owner ownership & disable ACLs (recommended default since 2023)
echo "Setting bucket ownership controls..."
aws s3api put-bucket-ownership-controls --bucket "$BUCKET" \
  --ownership-controls 'Rules=[{ObjectOwnership=BucketOwnerEnforced}]'

# 3  Block *public ACLs* but allow *public policies* (safer, policy-driven model)
echo "Configuring public access settings..."
aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration 'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=false,RestrictPublicBuckets=false'

# 4  Attach a read-only bucket policy just for the downloads/ prefix
echo "Creating bucket policy..."
cat > "$TEMP_DIR/public-downloads-policy.json" <<EOF
{
  "Version":"2012-10-17",
  "Statement":[{
    "Sid":"PublicReadDownloads",
    "Effect":"Allow",
    "Principal":"*",
    "Action":"s3:GetObject",
    "Resource":"arn:aws:s3:::$BUCKET/downloads/*"
  }]
}
EOF

# Use just one slash for file paths in Git Bash
policy_path=$(cygpath -m "$TEMP_DIR/public-downloads-policy.json")
aws s3api put-bucket-policy --bucket "$BUCKET" --policy "file://$policy_path"

# 5  Upload the .msi *without* any --acl flag (ACLs are disabled)
echo "Uploading MSI installer to S3..."
aws s3 cp "$LOCAL_MSI" "s3://$BUCKET/downloads/" \
  --content-type "application/x-msi" \
  --content-disposition 'attachment; filename="meld-4.5.0-windows64.msi"'

# Clean up
rm -rf "$TEMP_DIR"

echo "✅  Done!  Public URL:"
echo "    https://$BUCKET.s3.$REGION.amazonaws.com/downloads/$(basename "$LOCAL_MSI")" 