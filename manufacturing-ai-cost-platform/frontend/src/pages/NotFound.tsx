import { Link } from 'react-router-dom';

export function NotFound() {
  return (
    <div className="mx-auto flex max-w-[1400px] flex-col items-start gap-3 px-4 py-16 sm:px-6 lg:px-8">
      <h1 className="font-mono text-lg font-semibold tracking-tight text-foreground">
        Page not found
      </h1>
      <p className="text-sm text-muted-foreground">
        The page you&apos;re looking for doesn&apos;t exist.
      </p>
      <Link to="/dashboard" className="text-sm text-primary underline underline-offset-4">
        Back to the dashboard
      </Link>
    </div>
  );
}
