"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Chat demo" },
  { href: "/inbox", label: "Staff inbox" },
];

const TRACES_URL = process.env.NEXT_PUBLIC_TRACES_URL ?? "http://localhost:6006";

export function NavBar() {
  const pathname = usePathname();
  return (
    <header className="border-b border-border bg-surface">
      <div className="mx-auto flex h-14 w-full max-w-7xl items-center gap-3 px-4 sm:gap-6 sm:px-6">
        <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight whitespace-nowrap">
          <span className="grid size-7 place-items-center rounded-md bg-accent text-xs font-bold text-white">
            fd
          </span>
          openfrontdesk
        </Link>
        <nav className="flex items-center gap-1 text-sm">
          {LINKS.map((link) => {
            const active = pathname === link.href;
            return (
              <Link
                key={link.href}
                href={link.href}
                className={`rounded-md px-2.5 py-1.5 whitespace-nowrap transition-colors sm:px-3 ${
                  active
                    ? "bg-accent-soft font-medium text-accent"
                    : "text-muted hover:text-foreground"
                }`}
              >
                {link.label}
              </Link>
            );
          })}
        </nav>
        <a
          href={TRACES_URL}
          target="_blank"
          rel="noreferrer"
          className="ml-auto hidden text-sm text-muted hover:text-foreground sm:block"
        >
          Traces ↗
        </a>
      </div>
    </header>
  );
}
