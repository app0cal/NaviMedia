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
  output_path: string | null;
  dedupe_key: string;
  duplicate_of: number | null;
  allow_duplicate: boolean;
};

type SettingsResponse = {
  audio_format: string;
  audio_formats: string[];
};

type JobsResponse = { jobs: Job[] };
type RunResponse = { processed: number };
type ImportResponse = { imported: number; duplicates: number; errors: number };

type Notice = { kind: "success" | "error" | "info"; text: string } | null;

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

  async function loadJobs() {
    setLoadingJobs(true);
    try {
      const data = await api<JobsResponse>("/api/jobs?limit=50");
      setJobs(data.jobs);
    } finally {
      setLoadingJobs(false);
    }
  }

  async function loadSettings() {
    setLoadingSettings(true);
    try {
      setSettings(await api<SettingsResponse>("/api/settings"));
    } finally {
      setLoadingSettings(false);
    }
  }

  async function refreshAll() {
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
    event.preventDefault();
    if (!settings) return;
    setSavingSettings(true);
    setNotice(null);
    try {
      const result = await api<SettingsResponse>("/api/settings", {
        method: "PUT",
        body: JSON.stringify({ audio_format: settings.audio_format }),
      });
      setSettings(result);
      setNotice({ kind: "success", text: `Audio format saved as ${result.audio_format}.` });
    } catch (error) {
      setNotice({ kind: "error", text: messageFor(error) });
    } finally {
      setSavingSettings(false);
    }
  }

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
      await loadJobs();
    } catch (error) {
      setNotice({ kind: "error", text: messageFor(error) });
    } finally {
      setBusyAction(null);
    }
  }

  async function runQueued() {
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
    setBusyAction("import");
    setNotice(null);
    try {
      const result = await api<ImportResponse>("/api/import-queue", { method: "POST" });
      setNotice({
        kind: "success",
        text: `Imported ${result.imported}; duplicates ${result.duplicates}; errors ${result.errors}.`,
      });
      await loadJobs();
    } catch (error) {
      setNotice({ kind: "error", text: messageFor(error) });
    } finally {
      setBusyAction(null);
    }
  }

  async function retryJob(job: Job) {
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
      <header className="topbar">
        <div>
          <p className="eyebrow brand">NaviMedia</p>
          <h1>Navidrome Media Downloader</h1>
        </div>
        <div className="topbar-actions">
          <button className="button secondary" onClick={refreshAll} disabled={loadingJobs || Boolean(busyAction)}>
            Refresh
          </button>
        </div>
      </header>

      {notice && <div className={`notice ${notice.kind}`}>{notice.text}</div>}

      <section className="control-grid">
        <form className="panel submit-panel" onSubmit={submitJob}>
          <div className="panel-heading">
            <div>
              <h2>Submit URL</h2>
              <p>YouTube and Spotify links are supported.</p>
            </div>
            <SourceHint url={url} />
          </div>
          <label className="field-label" htmlFor="url">
            Media URL
          </label>
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
            <Toggle label="Queue only" checked={queueOnly} onChange={setQueueOnly} />
            <Toggle label="Allow duplicate redownload" checked={allowDuplicate} onChange={setAllowDuplicate} />
          </div>
          <p className="helper-text">
            Duplicate redownloads are still tracked in SQLite and stored separately by job ID.
          </p>
        </form>

        <section className="panel format-panel">
          <div className="panel-heading">
            <div>
              <h2>Audio Format</h2>
              <p>Choose the format future downloads will use.</p>
            </div>
            <span className="source-hint">Persisted</span>
          </div>
          <form className="format-form" onSubmit={saveSettings}>
            <label className="field-label" htmlFor="audio-format">
              Format
            </label>
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
            <p className="helper-text">
              Allowed formats: {settings?.audio_formats.join(", ") ?? "loading..."}.
            </p>
          </form>
        </section>
      </section>

      <section className="panel jobs-panel">
        <div className="jobs-toolbar">
          <div>
            <h2>Jobs</h2>
            <p>{loadingJobs ? "Loading jobs..." : `${jobs.length} recent job(s)`}</p>
          </div>
          <div className="job-actions">
            <button className="button secondary" onClick={runQueued} disabled={Boolean(busyAction)}>
              {busyAction === "run" ? "Running" : "Manual run"}
            </button>
            <button className="button secondary" onClick={importQueue} disabled={Boolean(busyAction)}>
              {busyAction === "import" ? "Importing" : "Import queue"}
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
      </section>
    </main>
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
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="toggle">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <span className="switch" />
      <span>{label}</span>
    </label>
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
            <th>URL</th>
            <th>Output</th>
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
              <td className="url-cell" title={job.raw_url}>
                {job.raw_url}
                {job.allow_duplicate && <span className="duplicate-marker">duplicate</span>}
              </td>
              <td className="muted-cell">{job.output_path ?? "—"}</td>
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

function messageFor(error: unknown) {
  return error instanceof Error ? error.message : "Unexpected error";
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
