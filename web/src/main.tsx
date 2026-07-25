import React, { FormEvent, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type Job = {
  id: number;
  source: string;
  raw_url: string;
  normalized_url: string;
  status: string;
  attempts: number;
  last_error: string | null;
  last_warning: string | null;
  output_path: string | null;
  duplicate_of: number | null;
  allow_duplicate: boolean;
  parent_id: number | null;
  child_count: number;
  child_created_count: number;
  child_duplicate_count: number;
  child_error_count: number;
  playlist_id: number | null;
  media_item_id: number | null;
  job_kind: string;
};

type Playlist = {
  id: number;
  source: string;
  raw_url: string;
  normalized_url: string;
  title: string | null;
  job_id: number | null;
  tracked: boolean;
  paused: boolean;
  interval_seconds: number;
  last_checked_at: string | null;
  last_success_at: string | null;
  next_check_at: string | null;
  last_error: string | null;
};

type PlaylistItem = {
  id: number;
  playlist_id: number;
  source: string;
  provider_id: string;
  url: string;
  title: string;
  artist: string;
  position: number;
  active: boolean;
  download_status: string;
  output_path: string | null;
  last_error: string | null;
  download_job_id: number | null;
};

type Settings = {
  audio_format: string;
  audio_formats: string[];
  thumbnail_mode: string;
  thumbnail_modes: string[];
  output_layout: string;
  output_layouts: string[];
  metadata_mode: string;
  metadata_modes: string[];
  playlist_mode: string;
  playlist_modes: string[];
  default_thumbnail_path: string;
};

type JobsResponse = {
  jobs: Job[];
  limit: number;
  offset: number;
  total: number;
  has_more: boolean;
};

type Notice = { kind: "success" | "error" | "info"; text: string } | null;
type ActiveView = "download" | "settings";
const JOB_PAGE_SIZE = 25;
const WEEK_SECONDS = 7 * 24 * 60 * 60;

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body?.detail ?? response.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body as T;
}

function App() {
  const [activeView, setActiveView] = useState<ActiveView>("download");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [tracked, setTracked] = useState<Playlist[]>([]);
  const [items, setItems] = useState<Record<number, PlaylistItem[]>>({});
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const expandedRef = useRef(expanded);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [url, setUrl] = useState("");
  const [queueOnly, setQueueOnly] = useState(false);
  const [allowDuplicate, setAllowDuplicate] = useState(false);
  const [trackPlaylist, setTrackPlaylist] = useState(false);
  const [intervalSeconds, setIntervalSeconds] = useState(WEEK_SECONDS);
  const [loadingJobs, setLoadingJobs] = useState(true);
  const [jobsLoaded, setJobsLoaded] = useState(false);
  const [loadingSettings, setLoadingSettings] = useState(true);
  const [savingSettings, setSavingSettings] = useState(false);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [jobOffset, setJobOffset] = useState(0);
  const [jobTotal, setJobTotal] = useState(0);
  const [hasMoreJobs, setHasMoreJobs] = useState(false);
  const isPlaylist = playlistUrl(url);

  useEffect(() => {
    expandedRef.current = expanded;
  }, [expanded]);

  async function loadJobs(offset = jobOffset) {
    setLoadingJobs(true);
    try {
      const data = await api<JobsResponse>(`/api/jobs?limit=${JOB_PAGE_SIZE}&offset=${offset}`);
      setJobs(data.jobs);
      setJobOffset(data.offset);
      setJobTotal(data.total);
      setHasMoreJobs(data.has_more);
      setJobsLoaded(true);
    } finally {
      setLoadingJobs(false);
    }
  }

  async function loadSettings() {
    setLoadingSettings(true);
    try {
      setSettings(await api<Settings>("/api/settings"));
    } finally {
      setLoadingSettings(false);
    }
  }

  async function loadTracked() {
    const data = await api<{ playlists: Playlist[] }>("/api/playlists/tracked");
    setTracked(data.playlists);
  }

  async function loadPlaylistItems(playlistId: number) {
    const data = await api<{ items: PlaylistItem[] }>(
      `/api/playlists/${playlistId}/items?limit=500`,
    );
    setItems((current) => ({ ...current, [playlistId]: data.items }));
  }

  async function refreshAll() {
    setNotice(null);
    try {
      await Promise.all([loadJobs(), loadTracked(), loadSettings()]);
      await Promise.all([...expandedRef.current].map(loadPlaylistItems));
    } catch (error) {
      setNotice({ kind: "error", text: messageFor(error) });
    }
  }

  useEffect(() => {
    refreshAll();
    const events = new EventSource("/api/events");
    let timer: number | undefined;
    const debounce = (callback: () => void) => {
      window.clearTimeout(timer);
      timer = window.setTimeout(callback, 120);
    };
    const refreshJobs = () => debounce(() => void Promise.all([loadJobs(), loadTracked()]));
    const refreshPlaylists = () => debounce(() => void Promise.all([loadTracked(), loadJobs()]));
    const refreshItems = (event: MessageEvent) => {
      let playlistId: number | undefined;
      try {
        playlistId = JSON.parse(event.data).playlist_id;
      } catch {
        return;
      }
      if (playlistId && expandedRef.current.has(playlistId)) {
        debounce(() => void loadPlaylistItems(playlistId!));
      }
    };
    events.addEventListener("connected", refreshJobs);
    events.addEventListener("job", refreshJobs);
    events.addEventListener("jobs", refreshJobs);
    events.addEventListener("playlist", refreshPlaylists);
    events.addEventListener("playlist-items", refreshItems as EventListener);
    return () => {
      window.clearTimeout(timer);
      events.close();
    };
  }, []);

  async function submitJob(event: FormEvent) {
    event.preventDefault();
    const trimmedUrl = url.trim();
    if (!trimmedUrl) {
      setNotice({ kind: "error", text: "Paste a YouTube or Spotify URL first." });
      return;
    }
    setBusyAction("submit");
    setNotice(null);
    try {
      const result = await api<{ created: boolean; accepted: boolean }>("/api/jobs", {
        method: "POST",
        body: JSON.stringify({
          url: trimmedUrl,
          queue_only: trackPlaylist ? false : queueOnly,
          allow_duplicate: allowDuplicate,
          track_playlist: trackPlaylist,
          interval_seconds: intervalSeconds,
        }),
      });
      setUrl("");
      setTrackPlaylist(false);
      setNotice({
        kind: "success",
        text: result.accepted
          ? `${result.created ? "Job created" : "Existing job refreshed"} and queued.`
          : "Job staged for a later manual run.",
      });
      await Promise.all([loadJobs(0), loadTracked()]);
    } catch (error) {
      setNotice({ kind: "error", text: messageFor(error) });
    } finally {
      setBusyAction(null);
    }
  }

  async function saveSettings(event: FormEvent) {
    event.preventDefault();
    if (!settings) return;
    setSavingSettings(true);
    try {
      setSettings(
        await api<Settings>("/api/settings", {
          method: "PUT",
          body: JSON.stringify({
            audio_format: settings.audio_format,
            thumbnail_mode: settings.thumbnail_mode,
            output_layout: settings.output_layout,
            metadata_mode: settings.metadata_mode,
            playlist_mode: settings.playlist_mode,
          }),
        }),
      );
      setNotice({ kind: "success", text: "Settings saved." });
    } catch (error) {
      setNotice({ kind: "error", text: messageFor(error) });
    } finally {
      setSavingSettings(false);
    }
  }

  async function runQueued() {
    await action("run", async () => {
      const result = await api<{ queued: number }>("/api/run", {
        method: "POST",
        body: JSON.stringify({ max_jobs: 1 }),
      });
      setNotice({ kind: "success", text: `Queued ${result.queued} staged job(s).` });
    });
  }

  async function importQueue() {
    await action("import", async () => {
      const result = await api<{ imported: number; duplicates: number; errors: number }>(
        "/api/import-queue",
        { method: "POST" },
      );
      setNotice({
        kind: "success",
        text: `Imported ${result.imported}; duplicates ${result.duplicates}; errors ${result.errors}.`,
      });
      await loadJobs(0);
    });
  }

  async function clearHistory() {
    if (
      !window.confirm(
        "Delete job history and URL dedupe history? Tracked playlists, schedules, membership, settings, and music files are kept.",
      )
    ) {
      return;
    }
    await action("clear-history", async () => {
      const result = await api<{ deleted_jobs: number }>("/api/jobs/clear-history", {
        method: "POST",
      });
      setNotice({ kind: "success", text: `Cleared ${result.deleted_jobs} job(s).` });
      await Promise.all([loadJobs(0), loadTracked()]);
    });
  }

  async function retryJob(job: Job) {
    await action(`retry-${job.id}`, async () => {
      await api(`/api/jobs/${job.id}/retry`, {
        method: "POST",
        body: JSON.stringify({ queue_only: false }),
      });
      setNotice({ kind: "success", text: `Queued retry for job ${job.id}.` });
    });
  }

  async function skipJob(job: Job) {
    const reason = window.prompt("Skip reason", "manual skip");
    if (reason === null) return;
    await action(`skip-${job.id}`, async () => {
      await api(`/api/jobs/${job.id}/skip`, {
        method: "POST",
        body: JSON.stringify({ reason: reason.trim() || "manual skip" }),
      });
      await loadJobs();
    });
  }

  async function updatePlaylist(playlist: Playlist, patch: Partial<Playlist>) {
    await action(`playlist-${playlist.id}`, async () => {
      await api(`/api/playlists/${playlist.id}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      });
      await loadTracked();
    });
  }

  async function checkPlaylist(playlist: Playlist) {
    await action(`check-${playlist.id}`, async () => {
      const result = await api<{ accepted: boolean }>(
        `/api/playlists/${playlist.id}/check`,
        { method: "POST" },
      );
      setNotice({
        kind: "success",
        text: result.accepted
          ? `Queued a check for ${playlist.title ?? "playlist"}.`
          : "A check is already queued or running.",
      });
    });
  }

  async function action(key: string, operation: () => Promise<void>) {
    setBusyAction(key);
    setNotice(null);
    try {
      await operation();
    } catch (error) {
      setNotice({ kind: "error", text: messageFor(error) });
    } finally {
      setBusyAction(null);
    }
  }

  async function toggleItems(playlistId: number) {
    const next = new Set(expanded);
    if (next.has(playlistId)) {
      next.delete(playlistId);
    } else {
      next.add(playlistId);
      await loadPlaylistItems(playlistId);
    }
    setExpanded(next);
  }

  const commonItemProps = { items, expanded, onToggle: toggleItems };
  const jobStart = jobs.length ? jobOffset + 1 : 0;
  const jobEnd = Math.min(jobOffset + jobs.length, jobTotal);
  const jobSummary = !jobsLoaded
    ? loadingJobs
      ? "Loading jobs..."
      : "Jobs unavailable."
    : loadingJobs
      ? `Refreshing jobs, showing ${jobStart}-${jobEnd}`
      : `${jobTotal} total, showing ${jobStart}-${jobEnd}`;

  return (
    <main className="app-shell">
      <SidebarNav activeView={activeView} onSelect={setActiveView} />
      <div className="app-content">
        <header className="topbar">
          <button className="button secondary" onClick={refreshAll} disabled={loadingJobs}>
            Refresh
          </button>
        </header>
        {notice && <div className={`notice ${notice.kind}`}>{notice.text}</div>}

        {activeView === "download" ? (
          <section className="view-stack" aria-label="Download">
            <form className="panel submit-panel" onSubmit={submitJob}>
              <div className="panel-heading">
                <div>
                  <h2>Submit URL</h2>
                  <p>YouTube, YouTube Music, and Spotify links are supported.</p>
                </div>
                <SourceHint url={url} />
              </div>
              <FieldLabel htmlFor="url" label="Media URL" />
              <div className="submit-row">
                <input
                  id="url"
                  value={url}
                  onChange={(event) => {
                    setUrl(event.target.value);
                    if (!playlistUrl(event.target.value)) setTrackPlaylist(false);
                  }}
                  placeholder="https://youtube.com/playlist?list=..."
                  disabled={busyAction === "submit"}
                />
                <button className="button primary" type="submit" disabled={busyAction === "submit"}>
                  {busyAction === "submit" ? "Submitting" : "Submit"}
                </button>
              </div>
              <div className="toggle-row">
                <Toggle
                  label="Queue only"
                  checked={queueOnly && !trackPlaylist}
                  onChange={setQueueOnly}
                  disabled={trackPlaylist}
                />
                <Toggle
                  label="Allow duplicate redownload"
                  checked={allowDuplicate && !trackPlaylist}
                  onChange={setAllowDuplicate}
                  disabled={trackPlaylist}
                />
                {isPlaylist && (
                  <Toggle
                    label="Track playlist"
                    checked={trackPlaylist}
                    onChange={(checked) => {
                      setTrackPlaylist(checked);
                      if (checked) {
                        setQueueOnly(false);
                        setAllowDuplicate(false);
                      }
                    }}
                  />
                )}
              </div>
              {trackPlaylist && (
                <div className="schedule-field">
                  <FieldLabel htmlFor="tracking-interval" label="Check interval" />
                  <IntervalEditor
                    id="tracking-interval"
                    seconds={intervalSeconds}
                    onChange={setIntervalSeconds}
                  />
                </div>
              )}
            </form>

            {tracked.length > 0 && (
              <section className="panel jobs-panel">
                <div className="panel-heading">
                  <div>
                    <h2>Tracked playlists</h2>
                    <p>Recurring checks and the latest known source membership.</p>
                  </div>
                </div>
                <TrackedTable
                  playlists={tracked}
                  busyAction={busyAction}
                  onCheck={checkPlaylist}
                  onUpdate={updatePlaylist}
                  {...commonItemProps}
                />
              </section>
            )}

            <section className="panel jobs-panel">
              <div className="jobs-toolbar">
                <div>
                  <h2>Jobs</h2>
                  <p>{jobSummary}</p>
                </div>
                <div className="job-actions">
                  <button className="button secondary" onClick={runQueued} disabled={Boolean(busyAction)}>
                    Manual run
                  </button>
                  <button className="button secondary" onClick={importQueue} disabled={Boolean(busyAction)}>
                    Import queue
                  </button>
                  <button
                    className="button secondary danger-button"
                    onClick={clearHistory}
                    disabled={Boolean(busyAction)}
                  >
                    Delete job history
                  </button>
                </div>
              </div>
              <JobTable
                jobs={jobs}
                loading={loadingJobs && !jobsLoaded}
                busyAction={busyAction}
                onRetry={retryJob}
                onSkip={skipJob}
                {...commonItemProps}
              />
              <div className="pagination-row">
                <button
                  className="button secondary"
                  onClick={() => loadJobs(Math.max(0, jobOffset - JOB_PAGE_SIZE))}
                  disabled={loadingJobs || jobOffset === 0}
                >
                  Previous
                </button>
                <span>
                  Page {Math.floor(jobOffset / JOB_PAGE_SIZE) + 1} of{" "}
                  {Math.max(1, Math.ceil(jobTotal / JOB_PAGE_SIZE))}
                </span>
                <button
                  className="button secondary"
                  onClick={() => loadJobs(jobOffset + JOB_PAGE_SIZE)}
                  disabled={loadingJobs || !hasMoreJobs}
                >
                  Next
                </button>
              </div>
            </section>
          </section>
        ) : (
          <SettingsPanel
            settings={settings}
            loading={loadingSettings}
            saving={savingSettings}
            onChange={setSettings}
            onSave={saveSettings}
          />
        )}
      </div>
    </main>
  );
}

type ItemExpansionProps = {
  items: Record<number, PlaylistItem[]>;
  expanded: Set<number>;
  onToggle: (playlistId: number) => void;
};

function TrackedTable({
  playlists,
  busyAction,
  onCheck,
  onUpdate,
  items,
  expanded,
  onToggle,
}: {
  playlists: Playlist[];
  busyAction: string | null;
  onCheck: (playlist: Playlist) => void;
  onUpdate: (playlist: Playlist, patch: Partial<Playlist>) => void;
} & ItemExpansionProps) {
  return (
    <div className="table-wrap">
      <table className="tracked-table">
        <thead>
          <tr>
            <th>Playlist</th>
            <th>State</th>
            <th>Interval</th>
            <th>Last check</th>
            <th>Next check</th>
            <th>Error</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {playlists.map((playlist) => (
            <React.Fragment key={playlist.id}>
              <tr>
                <td>
                  <button className="expand-button" onClick={() => onToggle(playlist.id)}>
                    <span aria-hidden>{expanded.has(playlist.id) ? "▾" : "▸"}</span>
                    {playlist.title ?? playlist.raw_url}
                  </button>
                </td>
                <td>
                  <span className={`status ${playlist.paused ? "skipped" : "complete"}`}>
                    {playlist.paused ? "paused" : "active"}
                  </span>
                </td>
                <td>
                  <IntervalEditor
                    id={`playlist-interval-${playlist.id}`}
                    seconds={playlist.interval_seconds}
                    compact
                    disabled={busyAction === `playlist-${playlist.id}`}
                    onChange={(seconds) =>
                      onUpdate(playlist, { interval_seconds: seconds })
                    }
                  />
                </td>
                <td>{formatTime(playlist.last_checked_at)}</td>
                <td>{playlist.paused ? "Paused" : formatTime(playlist.next_check_at)}</td>
                <td className="error-cell">{playlist.last_error ?? "—"}</td>
                <td>
                  <div className="row-actions">
                    <button className="link-button" onClick={() => onCheck(playlist)}>
                      Run now
                    </button>
                    <button
                      className="link-button"
                      onClick={() => onUpdate(playlist, { paused: !playlist.paused })}
                    >
                      {playlist.paused ? "Resume" : "Pause"}
                    </button>
                    <button
                      className="link-button danger"
                      onClick={() => onUpdate(playlist, { tracked: false })}
                    >
                      Stop
                    </button>
                  </div>
                </td>
              </tr>
              {expanded.has(playlist.id) && (
                <ItemExpansion items={items[playlist.id]} colSpan={7} />
              )}
            </React.Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function JobTable({
  jobs,
  loading,
  busyAction,
  onRetry,
  onSkip,
  items,
  expanded,
  onToggle,
}: {
  jobs: Job[];
  loading: boolean;
  busyAction: string | null;
  onRetry: (job: Job) => void;
  onSkip: (job: Job) => void;
} & ItemExpansionProps) {
  if (loading) return <div className="empty-state">Loading recent jobs...</div>;
  if (!jobs.length) return <div className="empty-state">No jobs yet.</div>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Source</th>
            <th>Status</th>
            <th>Playlist</th>
            <th>URL</th>
            <th>Output</th>
            <th>Warning / Error</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <React.Fragment key={job.id}>
              <tr>
                <td className="mono">#{job.id}</td>
                <td>{job.source}</td>
                <td>
                  <span className={`status ${job.status}`}>{job.status}</span>
                </td>
                <td>
                  {job.playlist_id ? (
                    <button className="expand-button" onClick={() => onToggle(job.playlist_id!)}>
                      <span aria-hidden>{expanded.has(job.playlist_id) ? "▾" : "▸"}</span>
                      {playlistContext(job)}
                    </button>
                  ) : (
                    playlistContext(job)
                  )}
                </td>
                <td className="url-cell" title={job.raw_url}>
                  {job.raw_url}
                  {job.allow_duplicate && <span className="duplicate-marker">duplicate</span>}
                </td>
                <td className="muted-cell">{job.output_path ?? "—"}</td>
                <td className={job.last_error ? "error-cell" : "warning-cell"}>
                  {job.last_error ?? job.last_warning ?? "—"}
                </td>
                <td>
                  <div className="row-actions">
                    <button
                      className="link-button"
                      onClick={() => onRetry(job)}
                      disabled={busyAction === `retry-${job.id}`}
                    >
                      Retry
                    </button>
                    <button className="link-button danger" onClick={() => onSkip(job)}>
                      Skip
                    </button>
                  </div>
                </td>
              </tr>
              {job.playlist_id && expanded.has(job.playlist_id) && (
                <ItemExpansion items={items[job.playlist_id]} colSpan={8} />
              )}
            </React.Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ItemExpansion({ items, colSpan }: { items?: PlaylistItem[]; colSpan: number }) {
  return (
    <tr className="item-expansion-row">
      <td colSpan={colSpan}>
        {!items ? (
          <div className="empty-state compact">Loading playlist items...</div>
        ) : !items.length ? (
          <div className="empty-state compact">No successful inventory yet.</div>
        ) : (
          <div className="playlist-items">
            <div className="playlist-item heading">
              <span>#</span>
              <span>Title</span>
              <span>Artist</span>
              <span>Membership</span>
              <span>Download</span>
            </div>
            {items.map((item) => (
              <div className="playlist-item" key={item.id}>
                <span className="mono">{item.position}</span>
                <span title={item.url}>{item.title}</span>
                <span>{item.artist}</span>
                <span>{item.active ? "Current" : "Removed"}</span>
                <span className={item.last_error ? "error-cell" : ""}>
                  {item.last_error ?? item.download_status}
                </span>
              </div>
            ))}
          </div>
        )}
      </td>
    </tr>
  );
}

function SettingsPanel({
  settings,
  loading,
  saving,
  onChange,
  onSave,
}: {
  settings: Settings | null;
  loading: boolean;
  saving: boolean;
  onChange: React.Dispatch<React.SetStateAction<Settings | null>>;
  onSave: (event: FormEvent) => void;
}) {
  const select = (key: keyof Settings, value: string) =>
    onChange((current) => (current ? { ...current, [key]: value } : current));
  return (
    <section className="view-stack settings-view" aria-label="Settings">
      <section className="panel format-panel">
        <div className="panel-heading">
          <div>
            <h2>Settings</h2>
            <p>Defaults used for future downloads.</p>
          </div>
        </div>
        <form className="format-form" onSubmit={onSave}>
          <SettingSelect label="Audio format" value={settings?.audio_format} options={settings?.audio_formats} onChange={(value) => select("audio_format", value)} />
          <SettingSelect label="Thumbnails" value={settings?.thumbnail_mode} options={settings?.thumbnail_modes} onChange={(value) => select("thumbnail_mode", value)} />
          <SettingSelect label="Library layout" value={settings?.output_layout} options={settings?.output_layouts} onChange={(value) => select("output_layout", value)} />
          <SettingSelect label="Metadata" value={settings?.metadata_mode} options={settings?.metadata_modes} onChange={(value) => select("metadata_mode", value)} />
          <SettingSelect label="Playlist handling" value={settings?.playlist_mode} options={settings?.playlist_modes} onChange={(value) => select("playlist_mode", value)} />
          <button className="button primary settings-save" type="submit" disabled={loading || saving || !settings}>
            {saving ? "Saving" : "Save settings"}
          </button>
        </form>
      </section>
    </section>
  );
}

function SettingSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value?: string;
  options?: string[];
  onChange: (value: string) => void;
}) {
  const id = label.toLowerCase().replace(/ /g, "-");
  return (
    <div className="stacked-field">
      <FieldLabel htmlFor={id} label={label} />
      <select id={id} value={value ?? ""} onChange={(event) => onChange(event.target.value)}>
        {(options ?? []).map((option) => (
          <option value={option} key={option}>{option.replace(/_/g, " ")}</option>
        ))}
      </select>
    </div>
  );
}

const INTERVAL_PRESETS = new Set([3600, 86400, WEEK_SECONDS, 2592000]);

function IntervalEditor({
  id,
  seconds,
  onChange,
  compact = false,
  disabled = false,
}: {
  id: string;
  seconds: number;
  onChange: (seconds: number) => void;
  compact?: boolean;
  disabled?: boolean;
}) {
  const isPreset = INTERVAL_PRESETS.has(seconds);
  const prefersDays = seconds % 86400 === 0;
  const unitSeconds = prefersDays ? 86400 : 3600;
  const amount = Math.max(1, Math.round(seconds / unitSeconds));
  return (
    <div className={`interval-editor ${compact ? "compact" : ""}`}>
      <select
        id={id}
        className={compact ? "compact-select" : ""}
        value={isPreset ? seconds : "custom"}
        disabled={disabled}
        onChange={(event) => {
          if (event.target.value === "custom") onChange(172800);
          else onChange(Number(event.target.value));
        }}
      >
        <option value={3600}>Hourly</option>
        <option value={86400}>Daily</option>
        <option value={WEEK_SECONDS}>Weekly</option>
        <option value={2592000}>Every 30 days</option>
        <option value="custom">Custom</option>
      </select>
      {!isPreset && (
        <>
          <input
            aria-label="Custom interval amount"
            type="number"
            min={1}
            max={prefersDays ? 365 : 8760}
            value={amount}
            disabled={disabled}
            onChange={(event) =>
              onChange(Math.max(1, Number(event.target.value)) * unitSeconds)
            }
          />
          <select
            aria-label="Custom interval unit"
            className={compact ? "compact-select" : ""}
            value={prefersDays ? "days" : "hours"}
            disabled={disabled}
            onChange={(event) =>
              onChange(amount * (event.target.value === "days" ? 86400 : 3600))
            }
          >
            <option value="hours">Hours</option>
            <option value="days">Days</option>
          </select>
        </>
      )}
    </div>
  );
}

function SidebarNav({ activeView, onSelect }: { activeView: ActiveView; onSelect: (view: ActiveView) => void }) {
  return (
    <aside className="sidebar" aria-label="Primary navigation">
      <div className="sidebar-brand">NaviMedia</div>
      <nav className="sidebar-nav">
        {(["download", "settings"] as ActiveView[]).map((view) => (
          <button
            className={`sidebar-link ${activeView === view ? "active" : ""}`}
            type="button"
            onClick={() => onSelect(view)}
            key={view}
          >
            {view[0].toUpperCase() + view.slice(1)}
          </button>
        ))}
      </nav>
    </aside>
  );
}

function SourceHint({ url }: { url: string }) {
  const value = url.toLowerCase();
  const label = value.includes("spotify") ? "Spotify" : value.includes("youtu") ? "YouTube" : "Unknown";
  return <span className={`source-hint ${label.toLowerCase()}`}>{label}</span>;
}

function Toggle({
  label,
  checked,
  onChange,
  disabled = false,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label className="toggle-control">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        disabled={disabled}
      />
      <span className="switch" />
      <span>{label}</span>
    </label>
  );
}

function FieldLabel({ htmlFor, label }: { htmlFor: string; label: string }) {
  return (
    <div className="field-label-row">
      <label className="field-label" htmlFor={htmlFor}>{label}</label>
    </div>
  );
}

function playlistUrl(value: string) {
  const lower = value.toLowerCase();
  return (
    (lower.includes("youtu") && (lower.includes("/playlist") || lower.includes("list="))) ||
    (lower.includes("open.spotify.com") && lower.includes("/playlist/"))
  );
}

function playlistContext(job: Job) {
  if (job.parent_id !== null) return `Child of #${job.parent_id}`;
  if (job.child_count > 0) {
    return `${job.child_created_count}/${job.child_count} new, ${job.child_error_count} errors`;
  }
  return job.job_kind === "playlist" ? "Playlist inventory" : "—";
}

function formatTime(value: string | null) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function messageFor(error: unknown) {
  return error instanceof Error ? error.message : "Unexpected error";
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
