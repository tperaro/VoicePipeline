# rclone → Google Drive probe: uploading to a folder shared with the user (edit access)

**Main findings:**
- **Target the folder by ID** (`root_folder_id`). Do not use `--drive-shared-with-me`.
- **Use `scope=drive`.** The narrower `drive.file` scope cannot write into a folder the app did not create.
- **Use a client_id of the user's own.** rclone's shared client_id is being retired during 2026, and it is already Sept 2026.
- **Quota:** uploaded files count against the uploader's own storage, not the folder owner's.

Scratch dir: `/tmp/claude-1000/-home-peras-gitperaro-thiago-knowledge/ed300675-58f2-4b29-8f05-0e27e4d61468/scratchpad/probes/rclone-drive/` (docs saved as text, `drive.go` v1.75.1 source, verified binary, `drive_probe.py`).

Evidence labels:
- **VERIFIED**: I ran it here.
- **SRC**: read in the rclone v1.75.1 source.
- **DOC**: quoted from documentation.
- **INFERRED**: not tested.

Nothing touched Google Drive: there is no account or config for that. The only local tests were copies on this machine. I did not modify `/home/peras/orochi-ia-homenagem`.

**One slip, already undone:** running `rclone config file` without `RCLONE_CONFIG` quietly created an empty `~/.config/rclone/` directory. I confirmed it was empty and created by me at 23:31:29, then removed it with `rmdir`. It no longer exists. So even rclone's read-only config commands create that directory.

## 1. Which folder to target

**Use `root_folder_id=<FOLDER_ID>`, and leave `shared_with_me` and `team_drive` empty.**
- DOC (rclone.org/drive): the root folder ID is "the directory (identified by its Folder ID) that rclone considers to be the root of your drive… set this to restrict rclone to a specific folder hierarchy… use `1Xyf…KHCh` as the `root_folder_id`".
- SRC (`NewFs`): when `root_folder_id` is set, rclone uses it directly and never looks up the real root.

**`--drive-shared-with-me` is not read-only, but it is the wrong tool here:**
- DOC: it "works both with the list… and the copy commands".
- SRC (`drive.go` ~L1043, `createFileInfo`): at the top level of the remote rclone only builds a `sharedWithMe=true` listing, which is virtual. A file written at that top level gets `Parents=[rootFolderID]`, which is the user's **My Drive root**.
- Writing to `remote:<FolderName>/x.mp4` does work, but it finds the folder **by name**, which is ambiguous if two shared items have the same name.
- **Never combine it with `root_folder_id`.** The folder would then be listed as "Shared with me" instead of its contents.

**Folder in someone's My Drive (normal case):**
- Files the user uploads are **owned by the user and use the user's quota** (15 GB free).
- DOC (Google One Help 9312312): "if you… add your own files into a folder someone shares with you, those new files use your storage."
- At 500 MB per video that is about 30 videos per 15 GB.

**Folder inside a Shared Drive:**
- DOC (Drive API, about-shareddrives): "Files in a shared drive belong to the organization… count against the organization's pooled storage."
- `root_folder_id=<folder>` alone should work. SRC: every list and create call sets `SupportsAllDrives(true)` and `IncludeItemsFromAllDrives(true)`. (INFERRED, not tested.)
- To target a whole Shared Drive, use `team_drive=<0A…>` instead.
- The user needs at least the Contributor role.

## 2. Scope

**Recommend `scope=drive`.**
- DOC (rclone): with `drive.file`, "rclone can read/view/modify only those files and folders it creates".
- DOC (Google): `drive.file` covers files "that you open with an app or that the user shares with an app while using the Google Picker API". rclone has no picker.
- Forum report of the same failure: `appNotAuthorizedToChild` (forum.rclone.org/t/6693).
- So `drive.file` will fail against a pre-existing shared folder.
- `drive` is a Google "Restricted" scope. For personal use that only means an "unverified app" warning on the consent screen (see §3).
- SRC: `scope=drive` requests only `https://www.googleapis.com/auth/drive`.

## 3. OAuth on the Linux desktop

**The shared client_id is being retired.**
- DOC (rclone.org/drive): "This shared client_id is being retired and will stop working during 2026. To avoid interruption you must create and use your own client_id, so creating one is now required rather than merely recommended."
- Changelog: v1.74.4 (2026-07-08) added the warning; v1.75.0 added it to the config wizard.
- DOC: each client_id has a global rate limit ("default Google quota is 10 transactions per second").

**Own client_id, unverified app:**
- DOC: for personal use "you can leave the app unverified, accept the warning screen, and publish it (rather than leaving it in 'Testing') to avoid the weekly grant expiry".
- In Testing mode, "any grants will expire after a week".
- If the "PUBLISH APP" button is greyed out, Google requires a homepage URL and a privacy-policy URL first.

**Creating the remote (it opens the browser):**
```
["rclone","config","create","iavoz","drive",
 "client_id=<CID>","client_secret=<SECRET>","scope=drive","root_folder_id=<FOLDER_ID>"]
 (+ "resource_key=<RK>" only if the link had one)
```
- SRC path through the wizard: `client_id` → `oauth` → `config_is_local` (default true) → `xdg-open http://127.0.0.1:53682/auth?state=…` with a local web server → "Configure this as a Shared Drive?" (default false) → done.
- DOC (`config create --help`): "if the config process would normally ask a question the default is taken". So it asks nothing on stdin; it only waits for the browser consent.
- If port 53682 is blocked, open it or use manual mode (DOC).
- The client_secret ends up on the command line, visible in `ps`. That is acceptable for a desktop client; Google treats installed-app secrets as non-confidential (INFERRED).

**Re-authenticating later:**
- `["rclone","config","reconnect","iavoz:"]` asks y/n questions on stdin, so it needs a terminal (SRC: calls `PostConfig`).
- GUI-friendly alternative: `["rclone","config","update","iavoz","config_refresh_token=true"]`. It takes the defaults, so it re-runs the browser flow. SRC: the "Token already configured - replace it?" question defaults to true. INFERRED, not run.
- When re-auth is needed, rclone's error text contains `rclone config reconnect iavoz:` or `invalid_grant` (SRC oauthutil L186/292/302/341). The wrapper should match on those strings.

## 4. Upload, idempotency, retries, logs

**Recommended upload:**
```
["rclone","copyto",LOCAL_MP4,"iavoz:"+DEST_NAME,
 "--use-json-log","--stats","1s","--stats-log-level","NOTICE","-v",
 "--retries","3","--retries-sleep","10s","--low-level-retries","10",
 "--drive-chunk-size","64M","--transfers","1"]
```
Optionally add `"--error-on-no-transfer"`: exit 9 then means "already up to date".

**How skipping works:**
- DOC: copy "does not transfer files that are identical… testing by size and modification time or MD5SUM".
- DOC: Drive stores modification times to 1 ms and supports MD5, SHA1 and SHA256.
- VERIFIED (locally):
  - Re-running on an identical file logs "Unchanged skipping" at debug level and exits 0.
  - With `--error-on-no-transfer` it exits 9.
  - After `touch` (same content, new mtime) it logs "Updated modification time in destination" and does not re-upload.
- `--checksum` compares size + MD5 instead. Drive already has the MD5, but rclone must read the whole local file to compute it.
- Recommendation: keep the default (size + mtime).

**copyto vs copy:**
- DOC: `copyto` copies to an exact destination name. VERIFIED: `copy file dst:` also works and keeps the file's own name.
- Use `copyto` so the Drive name is explicit.

**Duplicate names:**
- DOC: "Drive unlike all the other remotes can have duplicated files… Use `rclone dedupe`".
- SRC: `Put` first looks up the existing object. If found, it calls `Update`, which adds a new revision to the same file ID, so no duplicate is created.
- DOC: `--no-check-dest` "can cause duplicates… (e.g. Google Drive)". Do not use it.
- Two uploads of the same name at the same moment can still race and create duplicates (INFERRED). Serialize uploads (one at a time, with a lock) and use unique names, e.g. a timestamp.

**Retries and chunk size:**
- DOC: `--low-level-retries` retries a single HTTP request, "uploading a chunk of a big file for example". `--retries` re-runs the whole operation.
- DOC: `--drive-chunk-size` defaults to 8Mi, "Must a power of 2 >= 256k… each chunk is buffered in memory one per transfer".
- 64M means about 64 MiB of RAM and 8 chunks for a 500 MB file. Throughput against Drive was not measured.
- Uploads above `--drive-upload-cutoff` (8Mi) are resumable.

**JSON log (VERIFIED):**
- Everything goes to stderr, one JSON object per line. Stdout is empty.
- Stats lines carry `stats.{bytes,totalBytes,transfers,errors,transferring[]}`.
- The completion line is `{"msg":"Copied (new)","size":…,"object":…}`.
- With `-vv`, the first two lines are plain text, so the parser must skip non-JSON lines.
- `drive_probe.py::run_with_progress` parses this; the smoke test printed 64.4% → 100%, exit 0.

**Exit codes:**
- DOC: 0 ok, 1 other, 2 usage, 3 directory not found, 4 file not found, 5 retryable, 6 no-retry, 7 fatal, 8 max-transfer, 9 no transfer (only with `--error-on-no-transfer`), 10 max-duration.
- VERIFIED:
  - Unknown remote → 1.
  - Bad flag → 2.
  - Missing local file → 3, but only after 3 retries. Check `os.path.exists` before calling rclone.

**Verifying the upload:**
- DOC: rclone already checks checksums after transfer (`--ignore-checksum` turns that off).
- Explicit check: `["rclone","lsjson","iavoz:"+DEST_NAME,"--stat","--hash","--hash-type","md5","--original"]`. Compare `Size` and `Hashes.md5` with the local `hashlib.md5`.
- VERIFIED locally: the MD5 matches `md5sum`. On Drive, the `ID` field gives the link `https://drive.google.com/file/d/<ID>/view` (the `ID` part is INFERRED).
- For a whole folder: `rclone check LOCALDIR iavoz: --one-way`.
- **Do not use `rclone link`**: it creates a public "anyone with the link" share.

## 5. Installing without sudo

**Version (VERIFIED):**
- Latest stable is **v1.75.1**, released 2026-09-04 (`downloads.rclone.org/version.txt`).
- Official zip: `https://downloads.rclone.org/v1.75.1/rclone-v1.75.1-linux-amd64.zip`, or the alias `…/rclone-current-linux-amd64.zip`. The zip is 31,470,505 bytes; the binary is 85 MB, statically linked.

**Checking the download (VERIFIED):**
- `SHA256SUMS` is a **PGP clear-signed** file.
- In a scratch keyring, `gpg --verify` reported "Assinatura correta" (good signature) from Nick Craig-Wood. Key `FBF737ECE9F8AB18604BD2AC93935E02FF3B54FA` matches `dig key.rclone.org txt`.
- sha256 of the linux-amd64 zip: `982b5aa772841168f8e380f139e9e787b2a105403e32b94da8676a0e1c0a13ab`, OK.

**Commands:**
```
curl -fLO https://downloads.rclone.org/v1.75.1/rclone-v1.75.1-linux-amd64.zip
curl -fLO https://downloads.rclone.org/v1.75.1/SHA256SUMS
grep ' rclone-v1.75.1-linux-amd64.zip$' SHA256SUMS | sha256sum -c -
mkdir -p ~/.local/bin && unzip -j rclone-v1.75.1-linux-amd64.zip 'rclone-v1.75.1-linux-amd64/rclone' -d ~/.local/bin && chmod 755 ~/.local/bin/rclone
```
The `unzip -j` command was VERIFIED against a scratch directory.

**Ubuntu apt package: `1.60.1+dfsg-3ubuntu0.24.04.6`** (upstream release 2022-11-17). **Too old:**
- JSON "Copied" lines have no `size` field (added in v1.63).
- `--use-json-log` did not cover every log line until v1.68.
- It has no shared-client_id warnings, so it would fail silently once the shared client is shut down.

## 6. Folder link → ID

URL shapes (all 12 test cases in `drive_probe.py` pass; VERIFIED):
- `/drive/folders/<ID>?usp=sharing`
- `/drive/u/<N>/folders/<ID>`
- `/drive/mobile/folders/<ID>`
- `?usp=drive_link`
- `open?id=<ID>`
- `folderview?id=<ID>`
- `…?resourcekey=<RK>&usp=sharing`
- a bare ID

The parser rejects `/file/d/…` links, hosts other than Google, and links with no ID.

```python
_ID=r"[A-Za-z0-9_-]{10,}"
# host must be drive.google.com|docs.google.com; reject r"/file/d/"
folder = re.search(rf"/folders/({_ID})", path) or parse_qs(query)["id"][0]
rk = parse_qs(query).get("resourcekey",[None])[0]
```

**Resource keys:**
- DOC (rclone `--drive-resource-key`): "This resource key requirement only applies to a subset of old files… opening the folder once in the web interface… seems to be enough."
- DOC (Google): "Those who have recently viewed the file, or have direct access, don't need the resource key."
- A folder shared directly with the user's email does not need it. If the link has one, pass `resource_key=<RK>` anyway; it does no harm.

## 7. Security

- **Where the token lives:** in plain JSON (access + refresh token) in `~/.config/rclone/rclone.conf`.
  - SRC (`configfile.go`): a new file is created with mode `0600`; an existing file keeps its permissions.
  - The directory is created with `MkdirAll` (so 0755 after umask).
- **Keep it out of the project folder.** Do not pass `--config` inside the project, and do not put an `rclone.conf` next to the binary. DOC: the program directory is searched **first**.
- **Keep it out of logs:** never log the output of `rclone config show`. Use `rclone config redacted` when sharing a config.
- Optional: `rclone config encryption` / `--password-command` encrypts the config.
- The client_secret is not a real secret for a desktop app, but keep it out of git.

## Setup checklist for the user

1. Install rclone v1.75.1 into `~/.local/bin` with the four commands in §5.
2. Create a Google OAuth client (console.cloud.google.com):
   - Enable the Drive API.
   - Consent screen: External; add the `…/auth/drive` scope; add yourself as a test user.
   - Create a "Desktop app" client.
   - Audience → **PUBLISH APP**, so the grant does not expire after 7 days.
3. Copy the shared folder's link. The app extracts the ID, then runs `rclone config create iavoz drive client_id=… client_secret=… scope=drive root_folder_id=<ID>`. Sign in in the browser and click through "unverified app" (Advanced → Continue).
4. Test: `rclone lsjson iavoz: --max-depth 1` should exit 0. Then upload one video and check that its MD5 matches with `lsjson --stat --hash`.

Sources: https://rclone.org/drive/ , https://rclone.org/docs/ (#exit-code, #use-json-log), https://rclone.org/commands/rclone_copyto/ , https://rclone.org/commands/rclone_config_create/ , https://rclone.org/install/ , https://rclone.org/release_signing/ , https://rclone.org/changelog/ , https://github.com/rclone/rclone/blob/v1.75.1/backend/drive/drive.go , https://developers.google.com/workspace/drive/api/guides/api-specific-auth , https://developers.google.com/workspace/drive/api/guides/resource-keys , https://developers.google.com/workspace/drive/api/guides/about-shareddrives , https://support.google.com/googleone/answer/9312312 , https://forum.rclone.org/t/google-drive-changing-client-id-and-scope-drive-file-write-access/6693