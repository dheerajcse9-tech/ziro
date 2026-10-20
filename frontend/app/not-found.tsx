import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-3 px-4 text-center">
      <p className="font-mono text-sm text-muted">404</p>
      <h1 className="text-lg font-semibold">This page does not exist</h1>
      <Link href="/" className="text-sm text-accent hover:underline">Back to overview</Link>
    </div>
  );
}
