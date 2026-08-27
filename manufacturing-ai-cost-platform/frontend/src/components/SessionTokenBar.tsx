/**
 * Runtime bearer-token entry.
 *
 * Every protected endpoint needs a bearer token and this bundle ships without
 * one, by design: a token baked into `VITE_*` would be embedded in the
 * JavaScript and readable by anyone who loads the page
 * (AI_DEVELOPMENT_RULES.md section 19, SECURITY.md section 6). There is also no
 * login route — in a real deployment the host application supplies the token
 * from the identity provider.
 *
 * That left no way to see live data at all: `hasAuthToken()` was always false,
 * so every hook fell back to fixtures. This bar closes that gap for local work
 * and demonstrations without weakening the rule. The token is held in memory by
 * `apiClient` only — not localStorage, not sessionStorage, not the URL — so it
 * is gone on reload and cannot be read back out of the page by another script.
 *
 * Obtain one with:
 *   python -m app.security.dev_token --role ADMIN
 */

import { useState } from 'react';
import { mutate } from 'swr';
import { hasAuthToken, setAuthToken } from '../services/apiClient';
import { cn } from '../lib/utils';

export function SessionTokenBar() {
  const [value, setValue] = useState('');
  const [connected, setConnected] = useState(hasAuthToken());

  function apply(token: string | null) {
    setAuthToken(token);
    setConnected(hasAuthToken());
    setValue('');
    // Re-run every query so the switch between fixtures and live data is
    // immediate rather than waiting for the next refresh interval.
    void mutate(() => true, undefined, { revalidate: true });
  }

  return (
    <div className="flex items-center gap-2">
      <span
        className={cn(
          'rounded px-2 py-0.5 font-mono text-[11px] uppercase tracking-wide',
          connected
            ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
            : 'bg-amber-500/15 text-amber-600 dark:text-amber-400',
        )}
        title={
          connected
            ? 'Requests carry a bearer token. Pages show data returned by the backend.'
            : 'No token set. Pages fall back to local fixture data, labelled DEMO.'
        }
      >
        {connected ? 'Live API' : 'Demo fixtures'}
      </span>

      {connected ? (
        <button
          type="button"
          onClick={() => apply(null)}
          className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
        >
          Clear token
        </button>
      ) : (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            const token = value.trim();
            if (token) apply(token);
          }}
          className="flex items-center gap-1"
        >
          <input
            type="password"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder="Paste dev bearer token"
            aria-label="Development bearer token"
            autoComplete="off"
            spellCheck={false}
            className="w-44 rounded border border-border bg-background px-2 py-1 font-mono text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
          />
          <button
            type="submit"
            disabled={!value.trim()}
            className="rounded border border-border px-2 py-1 text-xs text-foreground disabled:opacity-40"
          >
            Connect
          </button>
        </form>
      )}
    </div>
  );
}
