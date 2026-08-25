import { Link } from 'react-router-dom';

export function NotFound() {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-6 dark:border-slate-700 dark:bg-slate-800">
      <h1 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
        Page not found
      </h1>
      <Link
        to="/status"
        className="mt-3 inline-block text-sm text-blue-700 underline dark:text-blue-400"
      >
        Back to platform status
      </Link>
    </div>
  );
}
