#!/usr/bin/env bash
# ── adjust these ───────────────────────────────────────────────────────────────
REGION="eu-north-1"                   # e.g. london; keep "us-east-1" if you prefer
BUCKET="meld-downloads"  # MUST be globally unique
LOCAL_DMG="/Users/sungeunchoi/projects/build_darwin/_CPack_Packages/Darwin/DragNDrop/meld-4.4.2-git20250429.fc01ab610c5e-arm64/meld-4.4.2-git20250429.fc01ab610c5e-arm64.dmg"
# ───────────────────────────────────────────────────────────────────────────────

# 1  Create the bucket
if [ "$REGION" = "us-east-1" ]; then
  aws s3api create-bucket --bucket "$BUCKET" --region "$REGION"
else
  aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
    --create-bucket-configuration LocationConstraint="$REGION"
fi   # :contentReference[oaicite:0]{index=0}

# 2  Enforce bucket-owner ownership & disable ACLs (recommended default since 2023)
aws s3api put-bucket-ownership-controls --bucket "$BUCKET" \
  --ownership-controls 'Rules=[{ObjectOwnership=BucketOwnerEnforced}]'     # :contentReference[oaicite:1]{index=1}

# 3  Block *public ACLs* but allow *public policies* (safer, policy-driven model)
aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration 'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=false,RestrictPublicBuckets=false'   # :contentReference[oaicite:2]{index=2}

# 4  Attach a read-only bucket policy just for the downloads/ prefix
cat > /tmp/public-downloads-policy.json <<EOF
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
aws s3api put-bucket-policy --bucket "$BUCKET" --policy file:///tmp/public-downloads-policy.json

# 5  Upload the .dmg *without* any --acl flag (ACLs are disabled)
aws s3 cp "$LOCAL_DMG" "s3://$BUCKET/downloads/" \
  --content-type application/x-apple-diskimage \
  --content-disposition 'attachment; filename="meld-4.5.0.dmg"'

echo "✅  Done!  Public URL:"
echo "    https://$BUCKET.s3.$REGION.amazonaws.com/downloads/$(basename "$LOCAL_DMG")"
