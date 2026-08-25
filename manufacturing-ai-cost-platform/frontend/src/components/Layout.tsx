import type { ReactNode } from 'react';
import { NavLink } from 'react-router-dom';

const NAV = [{ to: '/status', label: 'Status' }];

export function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-full bg-slate-50 dark:bg-slate-900">
      <header className="border-b border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-800">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
          <span className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            Manufacturing AI Cost Intelligence
          </span>
          <nav className="flex gap-4">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  isActive
                    ? 'text-sm font-medium text-blue-700 dark:text-blue-400'
                    : 'text-sm text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100'
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-8">{children}</main>
    </div>
  );
}
