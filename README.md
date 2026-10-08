<p align="center">
  <img src="brands/logo-wide.png" alt="UniFi AI Speaker" width="380">
</p>

# UniFi AI Speaker (Home Assistant custom integration)

Speaker-only control of the **UniFi AI Speaker / AI Horn Speaker** through the
local **UniFi Protect Integration API** — configured entirely through the
Home Assistant UI — plus a reliable "go silent on disarm" mechanism for use
with Alarmo.

> This integration is a deliberate, standalone **companion** to Home Assistant's
> official **UniFi Protect** integration. It does **not** replace, fork, modify,
> or duplicate it, and it creates **no** cameras, doorbells, NVR, or Protect
> sensors — only one device per AI Speaker. Run both at the same time.

---

## Quick start (UI only — no YAML)

1. Install the integration (HACS or manual — see [Installation](#installation)).
2. Restart Home Assistant if prompted.
3. **Settings → Devices & Services**.
4. **+ Add Integration**.
5. Search for **"UniFi AI Speaker"**.
6. Enter your Protect console host and API key (see [below](#create-a-unifi-protect-api-key)).
7. The speaker(s) are discovered automatically and appear as devices.
8. Open the integration's **⚙ Configure** to set alarm-mute behavior (restore
   delay, confirmation chime) — all dropdowns/sliders, no YAML.
9. Use the speaker's **Volume**, **Microphone**, and **Test sound** controls
   directly from its device page.

Everything below this point is reference material for that same UI, plus the
optional YAML path for advanced setups and Alarmo compatibility.

---

## Why this exists

The official UniFi Protect integration does not expose the AI Speaker with the
controls needed to **silence an Alarm Manager alarm when you disarm**. This
integration talks directly to the Protect Integration API to give you speaker
volume/mic controls and a safe disarm-mute workflow.

### The problem it solves

When Alarmo is disarmed, UniFi Alarm Manager keeps playing the alarm audio
(~1 minute clip, repeated 5× ≈ 5 minutes). This integration makes the speaker
**silent immediately** on disarm and then restores the exact previous volume.

### Why muting (and not "stop playback")

The current UniFi Protect Integration API (verified against the official
OpenAPI spec, v7.3.70) has **no endpoint to stop active Alarm Manager audio on a
speaker**. Speaker endpoints are read/patch/test-sound only. `play`/`stop` exist
only for the separate wireless **siren** device (`modelKey: "siren"`), *not* the
AI Horn Speaker (`modelKey: "speaker"`). So the supported approach is:

```
Disarm → save current volume → set volume 0 (instant silence)
       → wait (default 10 min) → restore the exact saved volume
       → optionally play a short confirmation chime (test sound)
```

The 10-minute default comfortably outlasts the ~5-minute repeated clip. It is
configurable in the integration options, along with the confirmation chime.

---

## Installation

### HACS (custom repository)
1. HACS → ⋮ → **Custom repositories**.
2. Add this repository, category **Integration**.
3. Install **UniFi AI Speaker**, then restart Home Assistant.

### Manual
Copy `custom_components/unifi_ai_speaker/` into your HA `config/custom_components/`
directory and restart Home Assistant.

---

## Create a UniFi Protect API key

1. Open your Protect console (e.g. `https://10.1.0.29`).
2. **Settings → Control Plane → Integrations** (UniFi Protect 5.3+).
3. Create an **API Key** and copy it.

The key authenticates via the `X-API-Key` header. **Keep it secret** — this
integration never logs it, never exposes it as an attribute, and redacts it from
diagnostics.

---

## Set up the integration

**Settings → Devices & Services → Add Integration → UniFi AI Speaker**

| Field | Example | Notes |
|---|---|---|
| Host / Console IP | `10.1.0.29` | Scheme/path added automatically. |
| API key | *(your key)* | Stored encrypted in the config entry. |
| Verify SSL certificate | **off** | Protect uses a self-signed cert on the LAN. Off skips TLS verification **for this console only** (via a dedicated no-verify aiohttp session). Turn it on only if your console presents a trusted certificate. Global certificate verification is never disabled. |

The setup tests the connection, authenticates against `/meta/info`, and
discovers your speaker(s). Clear, specific errors are shown for:

- **Cannot connect** — host unreachable or Integration API not enabled.
- **Invalid authentication** — API key rejected.
- **No speakers found** — connected fine, but no AI Speakers on this console.
- Any other unexpected response.

Multiple AI Speakers are all supported, each as its own device.

### Editing the connection later (Reconfigure)

If the host changes or you need to rotate the API key, you don't need to
delete and re-add the integration: open the entry's **⋮ menu → Reconfigure**.
If the stored API key is ever rejected (e.g. revoked in Protect), Home
Assistant automatically prompts you to **Re-authenticate** instead of silently
failing.

### Options (⚙ Configure on the integration)

These are the alarm-disarm mute settings, all configured via the UI:

| Option | Default | What it does |
|---|---|---|
| Restore delay | 600 seconds | How long to stay muted before restoring the saved volume. |
| How to silence the alarm on disarm | Play a test sound | Pick **Mute only** (ends silently) or **Play a test sound** (chimes first, then restores) — see below. |
| Test sound delay | 2 seconds | Wait before and after playing the chime. Only used when "Play a test sound" is selected. |
| Test sound volume | 30% | Volume to use **for the chime only** — independent of the speaker's real/alarm volume, which is restored right after. Only used when "Play a test sound" is selected. |
| Alarm panel for automatic mute | *(none)* | Pick your alarm panel for a fully GUI, no-YAML alarm setup — see [Alarmo setup](#alarmo-setup). |
| Alarm Manager webhook ID | *(none)* | Optional — lets `trigger_alarm` start your Alarm Manager automation through this integration instead of a separate `rest_command`. See [Alarmo setup](#alarmo-setup). |

**The two silence options, explained:**
- **Mute only** — the mute simply ends; volume goes back to normal, silently.
- **Play a test sound** — a speaker mounted for an alarm, doorbell, or
  announcement use may normally run quite loud, so playing the confirmation
  chime at that same volume would be startling and defeat the point of
  muting. Instead, the chime plays first at its own, typically much quieter,
  **Test sound volume**, and only afterward is the real volume restored.

You can also skip both and drive muting entirely yourself via the
`unifi_ai_speaker.*` actions — e.g. wired into an Alarmo action — which is
exactly [Option B](#option-b--explicit-alarmo-actions-advanced--multiple-panels)
below.

These are **global, entry-wide** settings — every speaker on a console shares
them. If you ever need different behavior for one specific disarm event, the
`mute_for_alarm_disarm` action/service accepts the same four values
(`restore_delay`, `play_test_sound`, `test_sound_delay`, `test_sound_volume`)
as optional per-call overrides.

---

## Entities (one device per speaker)

| Entity | Type | Notes |
|---|---|---|
| Volume | number (0–100) | `PATCH volume`. Unavailable while the speaker is disconnected. |
| Microphone volume | number (0–100) | Only if the speaker has a mic. |
| Microphone | switch | Only if the speaker has a mic. |
| Status | sensor (enum) | idle / streaming / playing / playing speech / uploading |
| Connection | sensor (enum, diagnostic) | connected / connecting / disconnected — stays available even when disconnected, so the problem is visible. |
| Mode | sensor (enum, diagnostic, off by default) | listen / talk |
| Alarm mute active | binary sensor (diagnostic) | On while muted; attributes show saved volume, restore time, and the confirmation-chime settings in effect. |
| Test sound | button | `POST test-sound` — same API call the alarm-mute lifecycle uses internally. |

Static details (model, MAC) live on the **device**, not as entities. Entities
use Material Design Icons via Home Assistant's icon-translation mechanism
(`icons.json`), so e.g. the microphone switch shows a crossed-out mic icon
when off, and the alarm-mute sensor shows a muted/unmuted speaker icon.

> **API limitation:** the Integration API's speaker object does **not** expose
> IP address, firmware version, or last-seen, so those diagnostic entities are
> intentionally not created.

---

## Actions / services

All target UniFi AI Speaker entities or devices — pick them from Home
Assistant's normal target picker (Settings → Automations & Scenes → Actions),
no need to type an entity ID.

| Action | What it does |
|---|---|
| `unifi_ai_speaker.mute_for_alarm_disarm` | Save current volume → set 0 → schedule restore. Optional fields: `restore_delay`, `play_test_sound`, `test_sound_delay`, `test_sound_volume` (each overrides the integration option for this call only). |
| `unifi_ai_speaker.restore_alarm_volume` | Restore the saved volume now and clear the mute. Plays the confirmation chime too, if enabled (same as an automatic restore). |
| `unifi_ai_speaker.cancel_alarm_mute` | Cancel the pending restore and restore immediately. Never plays the confirmation chime — used for re-arm/re-trigger, when a new alarm needs to be audible right away. |
| `unifi_ai_speaker.trigger_alarm` | POST the console's UniFi Alarm Manager webhook — the GUI-native alternative to a `rest_command`. Optional field `webhook_id` overrides the one configured in Options. The target only identifies *which console*; it doesn't touch speaker state itself. |

---

## Alarmo setup

Your existing alarm **trigger** (`rest_command.unifi_alarm`) keeps working
exactly as-is — nothing about it changes. This integration adds two things on
top: a GUI-native alternative to that `rest_command` for *triggering* the
alarm, and the **disarm → silence** half, each with a GUI and a YAML path.

### Triggering the alarm — GUI alternative to the rest_command

Your `rest_command.unifi_alarm` already works by POSTing to the Alarm
Manager's webhook URL using the host and API key you hand-maintain in
`configuration.yaml`. Since this integration already has that same host and
API key (from setup), you can trigger the same webhook through it instead —
all you need to add is the webhook ID itself:

1. In Protect, open **Alarm Manager** → your automation → the webhook action.
   The webhook URL ends in a GUID, e.g.
   `.../alarm-manager/webhook/695d5662-8940-42ef-a3e9-239cbd873d91` — that's
   the ID.
2. Paste it into the integration's **⚙ Configure → Alarm Manager webhook ID**.
3. Alarmo's **Triggered** action becomes:
   ```yaml
   service: unifi_ai_speaker.trigger_alarm
   data:
     entity_id: number.living_room_living_room_speaker_volume
   ```
   (the `entity_id` just identifies which console/integration entry to use —
   it doesn't touch the speaker itself.)

Your existing `rest_command.unifi_alarm` is untouched either way — use
whichever you prefer, or keep both for redundancy.

This integration adds the **disarm → silence** half regardless of which
trigger method you use — there are two ways to wire that up too.

### Option A — GUI only, no YAML (recommended)

On the integration's **⚙ Configure**, set **Alarm panel for automatic mute** to
your alarm panel (e.g. `alarm_control_panel.alarmo`), picked from a dropdown.
That's the entire setup. Nothing to add in Alarmo's action editor at all:

- When the panel goes from **triggered → disarmed**, every speaker on this
  entry is automatically muted and restored later — exactly the
  `mute_for_alarm_disarm` behaviour below, just triggered by the panel state
  instead of a manually-wired action.
- If the panel **re-arms or re-triggers** while a speaker is muted, the mute
  is cancelled and the real volume restored immediately, so the new alarm is
  audible. No separate re-trigger action to configure.
- A routine disarm that was **never triggered** (e.g. "arm away" → "disarm on
  arrival") does **not** mute anything — only an actual triggered → disarmed
  transition does.

This is the more reliable path: there's no YAML to typo, so a misconfigured
target entity can't silently make the mute never fire. It works with any
`alarm_control_panel` entity, not just Alarmo.

### Option B — Explicit Alarmo actions (advanced / multiple panels)

Leave **Alarm panel for automatic mute** empty and wire the actions into
Alarmo's action editor yourself — useful if you want different behavior per
panel, or to combine with other automations.

> **Example only — use your own entity IDs.** The examples below use this
> installation's actual entities (`number.living_room_living_room_speaker_volume`,
> `button.living_room_living_room_speaker_test_sound`) purely to show the real
> shape of a working config. Your speaker's entity IDs will be named after
> *your* speaker (check its device page), not these.

**Alarmo — Triggered action (unchanged, or switch to the GUI-configured version above):**
```yaml
service: rest_command.unifi_alarm
```

**Alarmo — Disarmed action:**
```yaml
service: unifi_ai_speaker.mute_for_alarm_disarm
data:
  entity_id: number.living_room_living_room_speaker_volume
  restore_delay: 600
```

Alarmo's action editor (and many existing automations) write `entity_id`
directly inside `data:` rather than under a `target:` key — both forms work
identically:

```yaml
service: unifi_ai_speaker.mute_for_alarm_disarm
target:
  entity_id: number.living_room_living_room_speaker_volume
data:
  restore_delay: 600
```

> Alarmo's editor uses the `service:` key (older style). Newer Home Assistant
> automations use `action:` instead — both map to the same call:
> ```yaml
> action: unifi_ai_speaker.mute_for_alarm_disarm
> data:
>   entity_id: number.living_room_living_room_speaker_volume
>   restore_delay: 600
> ```

**Re-trigger safety:** add a second Alarmo action on the **Armed/Triggered**
events so a new alarm isn't left muted:
```yaml
service: unifi_ai_speaker.cancel_alarm_mute
data:
  entity_id: number.living_room_living_room_speaker_volume
```

**Testing the speaker manually** — normal users should just press the **Test
sound** button on the device page, but it's also callable as a plain action if
you're scripting something:
```yaml
service: button.press
data:
  entity_id: button.living_room_living_room_speaker_test_sound
```

---

## How the restore and confirmation chime work

1. On disarm, the current volume is read (e.g. `63`) and saved.
2. Volume is PATCHed to `0` → instant silence.
3. A one-shot restore is scheduled `restore_delay` seconds later (default 600).
4. When it fires, **if the chime is enabled:**
   a. Volume is PATCHed to the **Test sound volume** (default 30%) — a
      separate, typically quiet level, never the speaker's real/alarm volume.
   b. The integration waits `test_sound_delay` seconds (default 2) for that
      change to apply, then calls the speaker's existing test-sound API
      directly — the same call the **Test sound** button makes, never a
      simulated button press.
   c. It waits `test_sound_delay` again so the chime actually has time to
      sound, **then** PATCHes volume to the real saved value (`63`) and
      refreshes the coordinator.
   If the chime is **disabled**, step 4 is just a single PATCH straight back
   to `63` — no detour through the quiet volume.

This order — quiet volume, then chime, then real volume — is deliberate: a
confirmation chime played at full alarm/doorbell volume would be as startling
as the alarm itself, defeating the point of muting.

State (`original_volume`, absolute `restore_deadline`, the confirmation-chime
settings in effect, and whether the chime has already played) is persisted to
Home Assistant storage, so a restart mid-mute — or mid-chime — does **not**
leave the speaker stuck at the wrong volume (see
[Restart behavior](#restart-behavior)). If the final real-volume write fails
and retries, the retry resumes at that step only; it never plays the chime a
second time.

**Manual restore** (the `restore_alarm_volume` action, or ending the mute
early) plays the confirmation chime too, if enabled — it's treated the same as
an automatic restore. **Cancel** (re-arm/re-trigger) never plays it and skips
the quiet-volume detour entirely, restoring the real volume directly, since a
new alarm is about to be audible anyway.

## How re-trigger protection works

- A **second disarm** while already muted **never** overwrites the saved volume
  with the current `0`; it keeps the first saved value and only re-extends the
  restore deadline (and refreshes the confirmation-chime settings for this
  mute, so a later service call can still change them).
- A **repeated disarm** never causes more than one confirmation chime: only
  the one restore that actually ends the mute plays it.
- `cancel_alarm_mute` (or the optional panel monitor) **cancels the pending
  timer** and restores the real volume **immediately**, so a new alarm is
  audible. If a restore was already in flight when the cancel arrives (a rare,
  sub-second race), it is invalidated via a per-speaker generation counter so
  it can never play the chime or clobber the cancel's own restore afterward.

## Restart behavior

On startup the integration reloads any saved mute:
- **Deadline already passed** → restore the saved volume immediately (playing
  the confirmation chime once, if it was enabled and hasn't played yet).
- **Deadline still in the future** → re-schedule the restore for the remaining time.

If the console/speaker is briefly unreachable at restore time, the restore is
retried (every 60s) and the saved state is kept until it succeeds; the
confirmation chime only plays once the volume write actually succeeds. State
files from before this chime feature existed load correctly too — missing
settings default to the standard on/2-seconds behavior.

---

## Troubleshooting

| Symptom | Check |
|---|---|
| `cannot_connect` at setup | Host reachable? Integration API enabled in Protect? Correct console IP? |
| `invalid_auth` | Regenerate the API key; ensure it has access. Home Assistant will prompt to re-authenticate automatically. |
| `no_speakers` | The console reports no `speaker` devices; confirm the AI Speaker is adopted. |
| Volume didn't restore | Check the **Alarm mute active** binary sensor's `restore_at` attribute and the HA log lines (`Temporarily muted…` / `Restored…`). |
| Speaker shows unavailable | Speaker `state` is not `CONNECTED`; its controls correctly go unavailable until it reconnects — check the **Connection** sensor, which stays available to show the disconnected state. |
| Need to change host/API key | Use the entry's **⋮ → Reconfigure** — no need to delete and re-add. |

---

## Security notes

- The API key is stored in the config entry and sent only as the `X-API-Key`
  header to your local console. It is **never** logged, exposed as an attribute,
  put in exceptions, or placed in diagnostics (diagnostics are redacted).
- TLS verification is disabled **only** when you choose *Verify SSL = off*, and
  only for this console's dedicated aiohttp session — global verification is
  never disabled.
- All communication is **local**; there is no cloud dependency, and no shell or
  subprocess usage.
- Your existing `rest_command.unifi_alarm` (and `unifi_alarm_disarm`) are **not**
  touched, renamed, or migrated.

---

## Verified UniFi Protect Integration API endpoints used

Base: `https://<console>/proxy/protect/integration/v1`

| Method | Path | Use |
|---|---|---|
| GET | `/meta/info` | validate auth / console version |
| GET | `/speakers` | discovery |
| GET | `/speakers/{id}` | refresh / read current volume |
| PATCH | `/speakers/{id}` | set `volume`, `micVolume`, `isMicEnabled`, `name` |
| POST | `/speakers/{id}/test-sound` | test sound (optional `volume`) — used by both the Test sound button and the post-restore confirmation chime |

**Not used / not available:** there is no speaker stop-playback endpoint and no
Alarm-Manager "is running / stop" endpoint in the current API; the alarm trigger
webhook (`POST /alarm-manager/webhook/{id}`) is handled by your existing
`rest_command`, not by this integration.

---

## Logo / Home Assistant branding

This integration ships an **original** speaker + sound-wave mark (not
Ubiquiti's trademarked logo, not Home Assistant's logo), and it's already
active — no extra setup needed.

Since **Home Assistant Core 2026.3.0**, a custom integration can bundle its
own icon/logo directly: Home Assistant serves any image found in a `brand/`
folder inside the integration itself
(`custom_components/unifi_ai_speaker/brand/`), before falling back to the
community [`home-assistant/brands`](https://github.com/home-assistant/brands)
repository or the default placeholder. No manifest change, no external PR, no
review wait — it just works on the next restart after installing. (On older
Home Assistant versions, before this folder is recognized, you'll see the
default integration icon instead; the integration itself is unaffected
either way.)

```
custom_components/unifi_ai_speaker/
├── brand/
│   ├── icon.png        256×256
│   ├── icon@2x.png      512×512
│   ├── logo.png        256×256 (optional)
│   └── logo@2x.png      512×512 (optional)
└── brands/              <- source assets for the above, plus logo.svg and
                             the wide README lockup; not read by Home Assistant
```

The scalable source (`brands/logo.svg`) and the README's wide lockup
(`brands/logo-wide.png`) live in the separate `brands/` folder at the repo
root — keep both in sync if you ever update the artwork.
