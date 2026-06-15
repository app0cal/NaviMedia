// Dashboard app for submitting jobs, managing settings, and reviewing job history.
import React, { FormEvent, useEffect, useState } from "react";
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
  dedupe_key: string;
  duplicate_of: number | null;
  allow_duplicate: boolean;
  parent_id: number | null;
  child_count: number;
  child_created_count: number;
  child_duplicate_count: number;
  child_error_count: number;
};

type SettingsResponse = {
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
type RunResponse = { processed: number };
type ImportResponse = { imported: number; duplicates: number; errors: number };
type ClearHistoryResponse = { deleted_jobs: number; archive_deleted: boolean };

type Notice = { kind: "success" | "error" | "info"; text: string } | null;
type ActiveView = "download" | "settings";
const JOB_PAGE_SIZE = 25;

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  // Fetch JSON from the local API and turn non-2xx responses into Error objects.
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
  // Own the dashboard state and coordinate API-backed user actions.
  const [activeView, setActiveView] = useState<ActiveView>("download");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [url, setUrl] = useState("");
  const [queueOnly, setQueueOnly] = useState(false);
  const [allowDuplicate, setAllowDuplicate] = useState(false);
  const [loadingJobs, setLoadingJobs] = useState(true);
  const [loadingSettings, setLoadingSettings] = useState(true);
  const [savingSettings, setSavingSettings] = useState(false);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [jobOffset, setJobOffset] = useState(0);
  const [jobTotal, setJobTotal] = useState(0);
  const [hasMoreJobs, setHasMoreJobs] = useState(false);
  const jobStart = jobs.length === 0 ? 0 : jobOffset + 1;
  const jobEnd = Math.min(jobOffset + jobs.length, jobTotal);

  async function loadJobs(offset = jobOffset) {
    // Load one paginated job page into local state.
    setLoadingJobs(true);
    try {
      const data = await api<JobsResponse>(`/api/jobs?limit=${JOB_PAGE_SIZE}&offset=${offset}`);
      setJobs(data.jobs);
      setJobOffset(data.offset);
      setJobTotal(data.total);
      setHasMoreJobs(data.has_more);
    } finally {
      setLoadingJobs(false);
    }
  }

  async function loadSettings() {
    // Load persisted runtime settings and allowed option values.
    setLoadingSettings(true);
    try {
      setSettings(await api<SettingsResponse>("/api/settings"));
    } finally {
      setLoadingSettings(false);
    }
  }

  async function refreshAll() {
    // Refresh jobs and settings together for the top-level Refresh action.
    setNotice(null);
    try {
      await Promise.all([loadJobs(), loadSettings()]);
    } catch (error) {
      setNotice({ kind: "error", text: messageFor(error) });
    }
  }

  useEffect(() => {
    refreshAll();
  }, []);

  async function saveSettings(event: FormEvent) {
    // Persist all runtime settings from the settings panel.
    event.preventDefault();
    if (!settings) return;
    setSavingSettings(true);
    setNotice(null);
    try {
      const result = await api<SettingsResponse>("/api/settings", {
        method: "PUT",
        body: JSON.stringify({
          audio_format: settings.audio_format,
          thumbnail_mode: settings.thumbnail_mode,
          output_layout: settings.output_layout,
          metadata_mode: settings.metadata_mode,
          playlist_mode: settings.playlist_mode,
        }),
      });
      setSettings(result);
      setNotice({
        kind: "success",
        text: `Settings saved: ${result.audio_format}, ${layoutLabel(result.output_layout)}, ${playlistLabel(result.playlist_mode)}.`,
      });
    } catch (error) {
      setNotice({ kind: "error", text: messageFor(error) });
    } finally {
      setSavingSettings(false);
    }
  }

  async function submitJob(event: FormEvent) {
    // Submit the URL form and optionally process the new job immediately.
    event.preventDefault();
    const trimmedUrl = url.trim();
    if (!trimmedUrl) {
      setNotice({ kind: "error", text: "Paste a YouTube or Spotify URL first." });
      return;
    }
    setBusyAction("submit");
    setNotice(null);
    try {
      const result = await api<{ created: boolean; processed: Job | null }>("/api/jobs", {
        method: "POST",
        body: JSON.stringify({
          url: trimmedUrl,
          queue_only: queueOnly,
          allow_duplicate: allowDuplicate,
        }),
      });
      setUrl("");
      const processedText = result.processed
        ? ` Processed with status ${result.processed.status}.`
        : "";
      setNotice({
        kind: "success",
        text: result.created ? `Job created.${processedText}` : "URL already exists; existing job was refreshed.",
      });
      await loadJobs(0);
    } catch (error) {
      setNotice({ kind: "error", text: messageFor(error) });
    } finally {
      setBusyAction(null);
    }
  }

  async function runQueued() {
    // Process one queued job through the manual run endpoint.
    setBusyAction("run");
    setNotice(null);
    try {
      const result = await api<RunResponse>("/api/run", {
        method: "POST",
        body: JSON.stringify({ max_jobs: 1 }),
      });
      setNotice({ kind: "success", text: `Processed ${result.processed} queued job(s).` });
      await loadJobs();
    } catch (error) {
      setNotice({ kind: "error", text: messageFor(error) });
    } finally {
      setBusyAction(null);
    }
  }

  async function importQueue() {
    // Import queue text files without immediately processing their jobs.
    setBusyAction("import");
    setNotice(null);
    try {
      const result = await api<ImportResponse>("/api/import-queue", { method: "POST" });
      setNotice({
        kind: "success",
        text: `Imported ${result.imported}; duplicates ${result.duplicates}; errors ${result.errors}.`,
      });
      await loadJobs(0);
    } catch (error) {
      setNotice({ kind: "error", text: messageFor(error) });
    } finally {
      setBusyAction(null);
    }
  }

  async function clearHistory() {
    // Delete job history through the backend so SQLite rows and yt-dlp archive stay in sync.
    const confirmed = window.confirm(
      "Delete all job history and URL dedupe history?\n\nThis also clears the YouTube download archive so URLs can be downloaded again. Music files, queue files, and settings will be kept.",
    );
    if (!confirmed) return;

    setBusyAction("clear-history");
    setNotice(null);
    try {
      const result = await api<ClearHistoryResponse>("/api/jobs/clear-history", { method: "POST" });
      setNotice({
        kind: "success",
        text: `Cleared ${result.deleted_jobs} job(s); YouTube archive ${
          result.archive_deleted ? "removed" : "was already absent"
        }.`,
      });
      await loadJobs(0);
    } catch (error) {
      setNotice({ kind: "error", text: messageFor(error) });
    } finally {
      setBusyAction(null);
    }
  }

  async function goToJobsPage(offset: number) {
    // Navigate to another job page while preserving current settings state.
    setNotice(null);
    try {
      await loadJobs(offset);
    } catch (error) {
      setNotice({ kind: "error", text: messageFor(error) });
    }
  }

  async function retryJob(job: Job) {
    // Retry one job immediately from the job table.
    setBusyAction(`retry-${job.id}`);
    setNotice(null);
    try {
      await api(`/api/jobs/${job.id}/retry`, {
        method: "POST",
        body: JSON.stringify({ queue_only: false }),
      });
      setNotice({ kind: "success", text: `Retried job ${job.id}.` });
      await loadJobs();
    } catch (error) {
      setNotice({ kind: "error", text: messageFor(error) });
    } finally {
      setBusyAction(null);
    }
  }

  async function skipJob(job: Job) {
    // Prompt for a skip reason and mark one job skipped.
    const reason = window.prompt("Skip reason", "manual skip");
    if (reason === null) return;
    setBusyAction(`skip-${job.id}`);
    setNotice(null);
    try {
      await api(`/api/jobs/${job.id}/skip`, {
        method: "POST",
        body: JSON.stringify({ reason: reason.trim() || "manual skip" }),
      });
      setNotice({ kind: "success", text: `Skipped job ${job.id}.` });
      await loadJobs();
    } catch (error) {
      setNotice({ kind: "error", text: messageFor(error) });
    } finally {
      setBusyAction(null);
    }
  }

  return (
    <main className="app-shell">
      <SidebarNav activeView={activeView} onSelect={setActiveView} />
      <div className="app-content">
        <header className="topbar">
          <div className="topbar-actions">
            <button className="button secondary" onClick={refreshAll} disabled={loadingJobs || Boolean(busyAction)}>
              Refresh
            </button>
          </div>
        </header>

        {notice && <div className={`notice ${notice.kind}`}>{notice.text}</div>}

        {activeView === "download" ? (
          <section className="view-stack download-view" aria-label="Download">
            <form className="panel submit-panel" onSubmit={submitJob}>
              <div className="panel-heading">
                <div>
                  <h2>Submit URL</h2>
                  <p>YouTube and Spotify links are supported.</p>
                </div>
                <SourceHint url={url} />
              </div>
              <FieldLabel htmlFor="url" label="Media URL" />
              <div className="submit-row">
                <input
                  id="url"
                  value={url}
                  onChange={(event) => setUrl(event.target.value)}
                  placeholder="https://youtube.com/watch?v=..."
                  disabled={busyAction === "submit"}
                />
                <button className="button primary" type="submit" disabled={busyAction === "submit"}>
                  {busyAction === "submit" ? "Submitting" : "Submit"}
                </button>
              </div>
              <div className="toggle-row">
                <Toggle
                  label="Queue only"
                  checked={queueOnly}
                  onChange={setQueueOnly}
                  help="Create the job without processing it immediately. Use Manual run later."
                />
                <Toggle
                  label="Allow duplicate redownload"
                  checked={allowDuplicate}
                  onChange={setAllowDuplicate}
                  help="Create a separate redownload job even if this URL already exists."
                />
              </div>
              <p className="helper-text">
                Duplicate redownloads are tracked in SQLite and stored separately by job ID.
              </p>
            </form>

            <section className="panel jobs-panel">
              <div className="jobs-toolbar">
                <div>
                  <h2>Jobs</h2>
                  <p>
                    {loadingJobs
                      ? "Loading jobs..."
                      : `${jobTotal} total job(s), showing ${jobStart}-${jobEnd}`}
                  </p>
                </div>
                <div className="job-actions">
                  <button className="button secondary" onClick={runQueued} disabled={Boolean(busyAction)}>
                    {busyAction === "run" ? "Running" : "Manual run"}
                  </button>
                  <button className="button secondary" onClick={importQueue} disabled={Boolean(busyAction)}>
                    {busyAction === "import" ? "Importing" : "Import queue"}
                  </button>
                  <button className="button secondary danger-button" onClick={clearHistory} disabled={Boolean(busyAction)}>
                    {busyAction === "clear-history" ? "Deleting history" : "Delete job history"}
                  </button>
                </div>
              </div>

              <JobTable
                jobs={jobs}
                loading={loadingJobs}
                busyAction={busyAction}
                onRetry={retryJob}
                onSkip={skipJob}
              />
              <div className="pagination-row">
                <button
                  className="button secondary"
                  onClick={() => goToJobsPage(Math.max(0, jobOffset - JOB_PAGE_SIZE))}
                  disabled={loadingJobs || jobOffset === 0}
                >
                  Previous
                </button>
                <span>
                  Page {Math.floor(jobOffset / JOB_PAGE_SIZE) + 1} of {Math.max(1, Math.ceil(jobTotal / JOB_PAGE_SIZE))}
                </span>
                <button
                  className="button secondary"
                  onClick={() => goToJobsPage(jobOffset + JOB_PAGE_SIZE)}
                  disabled={loadingJobs || !hasMoreJobs}
                >
                  Next
                </button>
              </div>
            </section>
          </section>
        ) : (
          <section className="view-stack settings-view" aria-label="Settings">
            <section className="panel format-panel" id="settings" tabIndex={-1}>
              <div className="panel-heading">
                <div>
                  <h2>Settings</h2>
                  <p>Set the defaults used for future downloads.</p>
                </div>
                <span className="source-hint">Persisted</span>
              </div>
              <form className="format-form" onSubmit={saveSettings}>
                <FieldLabel
                  htmlFor="audio-format"
                  label="Audio format"
                  help="Chooses the output file type for future audio downloads."
                />
                <div className="submit-row">
                  <select
                    id="audio-format"
                    value={settings?.audio_format ?? ""}
                    onChange={(event) =>
                      setSettings((current) =>
                        current ? { ...current, audio_format: event.target.value } : current,
                      )
                    }
                    disabled={loadingSettings || savingSettings || !settings}
                  >
                    {(settings?.audio_formats ?? []).map((format) => (
                      <option key={format} value={format}>
                        {format}
                      </option>
                    ))}
                  </select>
                  <button className="button primary" type="submit" disabled={loadingSettings || savingSettings || !settings}>
                    {savingSettings ? "Saving" : "Save"}
                  </button>
                </div>
                <FieldLabel
                  className="stacked-field"
                  htmlFor="thumbnail-mode"
                  label="Thumbnails"
                  help="Controls whether downloads keep source artwork, use /config/default.jpg, or skip artwork."
                />
                <select
                  id="thumbnail-mode"
                  value={settings?.thumbnail_mode ?? ""}
                  onChange={(event) =>
                    setSettings((current) =>
                      current ? { ...current, thumbnail_mode: event.target.value } : current,
                    )
                  }
                  disabled={loadingSettings || savingSettings || !settings}
                >
                  {(settings?.thumbnail_modes ?? []).map((mode) => (
                    <option key={mode} value={mode}>
                      {thumbnailLabel(mode)}
                    </option>
                  ))}
                </select>
                <FieldLabel
                  className="stacked-field"
                  htmlFor="output-layout"
                  label="Library layout"
                  help="Chooses how files are organized under the mounted Navidrome music folder."
                />
                <select
                  id="output-layout"
                  value={settings?.output_layout ?? ""}
                  onChange={(event) =>
                    setSettings((current) =>
                      current ? { ...current, output_layout: event.target.value } : current,
                    )
                  }
                  disabled={loadingSettings || savingSettings || !settings}
                >
                  {(settings?.output_layouts ?? []).map((layout) => (
                    <option key={layout} value={layout}>
                      {layoutLabel(layout)}
                    </option>
                  ))}
                </select>
                <FieldLabel
                  className="stacked-field"
                  htmlFor="metadata-mode"
                  label="Metadata"
                  help="Controls whether source metadata is preserved or cleaned for Navidrome indexing."
                />
                <select
                  id="metadata-mode"
                  value={settings?.metadata_mode ?? ""}
                  onChange={(event) =>
                    setSettings((current) =>
                      current ? { ...current, metadata_mode: event.target.value } : current,
                    )
                  }
                  disabled={loadingSettings || savingSettings || !settings}
                >
                  {(settings?.metadata_modes ?? []).map((mode) => (
                    <option key={mode} value={mode}>
                      {metadataLabel(mode)}
                    </option>
                  ))}
                </select>
                <FieldLabel
                  className="stacked-field"
                  htmlFor="playlist-mode"
                  label="Playlist handling"
                  help="Chooses whether playlists download as one job or expand supported YouTube playlists into child jobs."
                />
                <select
                  id="playlist-mode"
                  value={settings?.playlist_mode ?? ""}
                  onChange={(event) =>
                    setSettings((current) =>
                      current ? { ...current, playlist_mode: event.target.value } : current,
                    )
                  }
                  disabled={loadingSettings || savingSettings || !settings}
                >
                  {(settings?.playlist_modes ?? []).map((mode) => (
                    <option key={mode} value={mode}>
                      {playlistLabel(mode)}
                    </option>
                  ))}
                </select>
                <p className="helper-text">
                  Default art path: {settings?.default_thumbnail_path ?? "loading..."}. Spotify playlists still use
                  single-job handling.
                </p>
              </form>
            </section>
          </section>
        )}
      </div>
    </main>
  );
}

function SidebarNav({
  activeView,
  onSelect,
}: {
  activeView: ActiveView;
  onSelect: (view: ActiveView) => void;
}) {
  // Provide the persistent dashboard navigation rail.
  return (
    <aside className="sidebar" aria-label="Primary navigation">
      <div className="sidebar-brand">NaviMedia</div>
      <nav className="sidebar-nav">
        <button
          className={`sidebar-link ${activeView === "download" ? "active" : ""}`}
          type="button"
          onClick={() => onSelect("download")}
        >
          Download
        </button>
        <button
          className={`sidebar-link ${activeView === "settings" ? "active" : ""}`}
          type="button"
          onClick={() => onSelect("settings")}
        >
          Settings
        </button>
      </nav>
      <div className="sidebar-footer">
        <button className="sidebar-link" type="button" disabled>
          Sign out
        </button>
      </div>
    </aside>
  );
}

function SourceHint({ url }: { url: string }) {
  // Show a lightweight source guess while the user types.
  const value = url.toLowerCase();
  const label = value.includes("spotify") ? "Spotify" : value.includes("youtu") ? "YouTube" : "Unknown";
  return <span className={`source-hint ${label.toLowerCase()}`}>{label}</span>;
}

function thumbnailLabel(mode: string) {
  // Convert thumbnail mode ids into dashboard labels.
  if (mode === "source") return "Use source thumbnails";
  if (mode === "default") return "Use default image";
  if (mode === "none") return "No thumbnails";
  return mode;
}

function layoutLabel(layout: string) {
  // Convert output layout ids into dashboard labels.
  if (layout === "artist_album_folders") return "Navidrome organized";
  if (layout === "creator_folders") return "Creator folders";
  if (layout === "source_folders") return "Source folders / legacy";
  return layout;
}

function metadataLabel(mode: string) {
  // Convert metadata mode ids into dashboard labels.
  if (mode === "source") return "Source metadata";
  if (mode === "navidrome_clean") return "Navidrome clean";
  return mode;
}

function playlistLabel(mode: string) {
  // Convert playlist mode ids into dashboard labels.
  if (mode === "single_job") return "Download playlist as one job";
  if (mode === "expand_items") return "Expand playlist into queued items";
  return mode;
}

function Toggle({
  label,
  checked,
  onChange,
  help,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  help?: string;
}) {
  // Render a reusable labeled switch control.
  return (
    <div className="toggle">
      <label className="toggle-control">
        <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
        <span className="switch" />
        <span>{label}</span>
      </label>
      {help && <InfoHint label={label} text={help} />}
    </div>
  );
}

function FieldLabel({
  htmlFor,
  label,
  help,
  className = "",
}: {
  htmlFor: string;
  label: string;
  help?: string;
  className?: string;
}) {
  // Pair field labels with optional accessible help popups.
  return (
    <div className={`field-label-row ${className}`}>
      <label className="field-label" htmlFor={htmlFor}>
        {label}
      </label>
      {help && <InfoHint label={label} text={help} />}
    </div>
  );
}

function InfoHint({ label, text }: { label: string; text: string }) {
  // Show a compact help bubble on hover and keyboard focus.
  return (
    <span className="info-hint">
      <button className="info-button" type="button" aria-label={`${label} help`}>
        ?
      </button>
      <span className="info-popup" role="tooltip">
        {text}
      </span>
    </span>
  );
}

function JobTable({
  jobs,
  loading,
  busyAction,
  onRetry,
  onSkip,
}: {
  jobs: Job[];
  loading: boolean;
  busyAction: string | null;
  onRetry: (job: Job) => void;
  onSkip: (job: Job) => void;
}) {
  // Render paginated jobs as a flat table with parent/child context.
  if (loading) {
    return <div className="empty-state">Loading recent jobs...</div>;
  }
  if (jobs.length === 0) {
    return <div className="empty-state">No jobs yet. Submit a URL or import queue files to start.</div>;
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Source</th>
            <th>Status</th>
            <th>Attempts</th>
            <th>Parent / Children</th>
            <th>URL</th>
            <th>Output</th>
            <th>Warning</th>
            <th>Error</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.id}>
              <td className="mono">#{job.id}</td>
              <td>{job.source}</td>
              <td>
                <span className={`status ${job.status}`}>{job.status}</span>
              </td>
              <td>{job.attempts}</td>
              <td className="muted-cell">{playlistContext(job)}</td>
              <td className="url-cell" title={job.raw_url}>
                {job.raw_url}
                {job.allow_duplicate && <span className="duplicate-marker">duplicate</span>}
              </td>
              <td className="muted-cell">{job.output_path ?? "—"}</td>
              <td className="warning-cell">{job.last_warning ?? "—"}</td>
              <td className="error-cell">{job.last_error ?? "—"}</td>
              <td>
                <div className="row-actions">
                  <button
                    className="link-button"
                    onClick={() => onRetry(job)}
                    disabled={busyAction === `retry-${job.id}`}
                  >
                    Retry
                  </button>
                  <button
                    className="link-button danger"
                    onClick={() => onSkip(job)}
                    disabled={busyAction === `skip-${job.id}`}
                  >
                    Skip
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function playlistContext(job: Job): string {
  // Summarize parent/child playlist expansion state for one job row.
  if (job.parent_id !== null) {
    return `child of #${job.parent_id}`;
  }
  if (job.child_count > 0 || job.child_created_count > 0) {
    return `${job.child_created_count}/${job.child_count} queued, ${job.child_duplicate_count} dupes, ${job.child_error_count} errors`;
  }
  return "—";
}

function messageFor(error: unknown) {
  // Normalize thrown values into displayable notice text.
  return error instanceof Error ? error.message : "Unexpected error";
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
