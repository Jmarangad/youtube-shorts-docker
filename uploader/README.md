# youtube-shorts-uploader

Uploads the Hindi-dubbed Shorts produced by
[`youtube-shorts-dubber`](../youtube-shorts-dubber) to YouTube.

For each dubbed MP4 it:

1. Reads the file name (which is derived from the trending agent's
   `latest.json` title) to recover the source title and its hashtags.
2. Translates the descriptive title text into **Hindi** and uses that as
   the YouTube video title.
3. Copies the trending **hashtags** into the video's tags and description.
4. Uploads the video via the YouTube Data API v3
   (`videos.insert`, category *People & Blogs*).

Videos already recorded in `upload-manifest.json` are skipped, so re-runs
never double-upload the same file.

## YouTube OAuth credentials (required)

Uploading needs **OAuth 2.0**, not the API key — the API key can only read
data. Set up a Google Cloud project:

1. Create a project at <https://console.cloud.google.com/>.
2. Enable the **YouTube Data API v3**.
3. Under *APIs & Services → Credentials*, create an **OAuth 2.0 Client ID**
   of type *Desktop app*. Download the JSON as `client_secret.json`.
4. Generate a one-time token. The helper below opens a browser for the
   consent screen and writes `token.json`:

   ```bash
   python -m uploader.auth_flow   # saves token.json next to client_secret.json
   ```

5. Mount a directory containing **both** `client_secret.json` and
   `token.json` at the uploader's `--config-dir` (the compose service uses
   the `uploader-config` volume).

Privacy defaults to `unlisted` (override with `--privacy public|private` or
the `YT_PRIVACY` env var).

## Usage

```bash
.venv/bin/python -m uploader --dry-run            # preview titles/tags, no upload
.venv/bin/python -m uploader                       # real upload (needs creds)
.venv/bin/python -m uploader --privacy public
.venv/bin/python -m uploader --install-cron        # daily 23:50 job
```

Results are written to `upload-manifest.json` in the dubbed directory.
