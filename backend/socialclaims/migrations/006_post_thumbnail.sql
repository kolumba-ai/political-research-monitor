-- Oriane thumbnail per post (thumbnailMediaUrl/Id) and the local copy downloaded by `socialclaims thumbnails`.
ALTER TABLE post ADD COLUMN IF NOT EXISTS thumbnail_url TEXT;
ALTER TABLE post ADD COLUMN IF NOT EXISTS thumbnail_media_id TEXT;
ALTER TABLE post ADD COLUMN IF NOT EXISTS thumbnail_path TEXT;
