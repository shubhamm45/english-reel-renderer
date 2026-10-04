# Daily English Reel Renderer

Design v2 uses a warm cream background, coral/mint/ink lesson panels, larger headings, learning cues, three-step navigation, and gentle zoom/fade motion.

This bundle renders a narrated, three-card, 1080 × 1920 English lesson as an H.264/AAC MP4. GitHub Actions publishes it through GitHub Pages. Each render has its own request ID and result URL; Make must use that result, not an unrelated `latest.json`.

## 1. Upload and enable GitHub Pages

1. Create a public GitHub repository with default branch `main`.
2. Copy this folder's contents into the repository root. Include the hidden `.github/workflows/render-reel.yml` file. Do not upload the ZIP as a single repository file.
3. Open Settings → Pages → Build and deployment → Source → **GitHub Actions**.
4. In Settings → Actions → General, ensure repository policy allows the workflow's write permissions. If deployment needs approval, approve the `github-pages` environment or adjust its protection settings.
5. Open Actions → Render English Reel → Run workflow. Enter a fresh request ID, such as `demo-20261004-001`, and the JSON from `sample-lesson.json`.
6. Wait for **Deploy Pages** to succeed. Use the Pages URL shown by the deployment; custom domains and user repositories may have a different base URL than `https://OWNER.github.io/REPO`.
7. Open `PAGES_BASE_URL/requests/demo-20261004-001.json`. Open its `video_url` and verify that the reel plays with speech.

The workflow stores previously rendered files in a generated `gh-pages` branch and deploys them using the official Pages actions. Use this repository only for this renderer. It preserves all old videos so that a new render does not remove a video Instagram is still fetching. Periodically remove old files from `gh-pages` once publishing is confirmed; rerun with a fresh ID to update the deployed site. This is a small-volume starting point, not unlimited video storage.

## 2. Qwen output

Ask Qwen to return only this JSON object, without markdown fences:

```json
{
  "title": "Sound more natural in English",
  "phrase": "I'm on my way.",
  "meaning": "Use this when you have started travelling to meet someone.",
  "example": "I'm on my way. I'll be there in ten minutes."
}
```

Title: maximum 100 characters. Other fields: maximum 240 each. Use English text supported by the bundled DejaVu font. The renderer rejects oversized text and videos above 90 seconds or 90 MiB.

## 3. Make: dispatch the render

Create a fresh UUID once per lesson and keep it in a variable or Google Sheets row. Never regenerate it inside the polling step and never reuse an ID from an earlier successful render.

HTTP request:

- Method: `POST`
- URL: `https://api.github.com/repos/OWNER/REPO/actions/workflows/render-reel.yml/dispatches`
- `Accept`: `application/vnd.github+json`
- `Authorization`: `Bearer YOUR_TOKEN`
- `X-GitHub-Api-Version`: `2022-11-28`
- `Content-Type`: `application/json`

Use a fine-grained GitHub token scoped to this repository with **Actions: read and write**. Save it in Make's authenticated connection/credential facility. Do not put tokens in committed files or exported blueprints.

`make-dispatch-body.json` is a sample HTTP body, **not an importable Make blueprint**. Replace its request ID and lesson with your mapped values. Use Make's JSON builder to serialize the complete request; `inputs.lesson_json` is a JSON **string**, so the builder must escape it correctly. Do not concatenate model-generated text into raw JSON.

A successful dispatch response starts the workflow. Its response body is not the video URL.

## 4. Make: wait for the correct result

Read:

`PAGES_BASE_URL/requests/REQUEST_ID.json`

A successful result has this form:

```json
{
  "request_id": "demo-20261004-001",
  "status": "ready",
  "video_url": "https://OWNER.github.io/REPO/reels/demo-20261004-001/reel.mp4",
  "test_audio": false
}
```

For a simple, dependable Make setup, use two scenarios:

**Scenario A — create lesson:** Google Sheets/history → Qwen → Parse JSON → create and store request ID, lesson/caption and pending status in Sheets → dispatch GitHub workflow. Run sequentially, with only one outstanding render at a time. If dispatch fails, mark that row failed rather than leaving it pending.

**Scenario B — finish publishing:** Run every minute, sequentially. Read pending rows → GET request-specific result JSON → parse it → require matching request ID, `status = ready`, `test_audio = false`, and row status still pending → set row status to publishing → Instagram Reel with `video_url` → save Instagram result and set row to published.

Handle HTTP 404 as “still waiting” and leave the row pending; deployment has not finished or propagated. Use a changing query parameter if cached 404s persist. Other HTTP errors need explicit error handling. Stop waiting after 20 minutes, mark the row failed, and inspect the matching GitHub Actions run. A missing JSON is never permission to publish an earlier video.

Disable overlapping runs of the publishing scenario. If Instagram fails or returns an uncertain result after the row becomes publishing, record the error and review that row before retrying; blindly retrying could duplicate a post. Write lesson history only after confirmed Instagram success.

For the Instagram Page field, use the Facebook Page ID connected to the Instagram professional account. Map only the returned `video_url` to Video URL. Confirm the URL is publicly fetchable before the first real publish.

A single-scenario polling loop is also possible, but its module IDs, routes and error handlers must be built against your actual Make blueprint. The original blueprint was not provided with the pasted conversation, so this bundle does not claim to replace it.

## 5. Local checks

Requires Python 3.12, FFmpeg/ffprobe and DejaVu fonts (or set `REEL_FONT` to a suitable TTF).

```sh
pip install -r requirements.txt
python render_reel.py --lesson sample-lesson.json --request-id local-demo-001 --base-url https://example.com --public public
```

`edge-tts` uses Microsoft's online speech service via an unofficial client. It does not require an API key, but needs internet access and can fail if the service changes. The renderer retries three times and fails the job if narration fails. Replace it with your chosen supported TTS provider if production reliability requires that.

For offline media verification only, add `--test-audio`. This generates tones instead of speech and marks the manifest `test_audio: true`; never map such a result to Instagram.

## Included files

- `.github/workflows/render-reel.yml`: dispatch, render, preserve and deploy
- `render_reel.py`: narration, card rendering, encoding and validation
- `requirements.txt`: Python dependencies
- `sample-lesson.json`: lesson schema/example
- `make-dispatch-body.json`: example GitHub HTTP request body
- `README.md`: setup and Make integration

No GitHub credentials, WordPress passwords or Instagram tokens are included. Live GitHub/Make/Instagram verification requires your actual repository and Make blueprint.
