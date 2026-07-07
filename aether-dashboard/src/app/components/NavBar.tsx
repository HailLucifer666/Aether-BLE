"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, MapPin, Radio, SlidersHorizontal, Wrench } from "lucide-react";
import type { LucideIcon } from "lucide-react";

interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
}

const NAV_ITEMS: readonly NavItem[] = [
  { href: "/spatial", label: "Spatial", icon: MapPin },
  { href: "/signal-lab", label: "Signal Lab", icon: SlidersHorizontal },
  { href: "/timeline", label: "Timeline", icon: Activity },
  { href: "/setup", label: "Setup", icon: Wrench },
  { href: "/mesh", label: "Mesh (fallback)", icon: Radio },
];

/** Top nav shared by every Phase 10 route. Purely a router — carries no
 * connection/election state of its own. */
export default function NavBar() {
  const pathname = usePathname();

  return (
    <nav className="sticky top-0 z-20 border-b border-white/5 bg-[#0a0f1e]/80 backdrop-blur-md">
      <div className="mx-auto flex max-w-6xl items-center gap-1 overflow-x-auto px-4 py-3">
        <span className="mr-3 flex items-center gap-2 whitespace-nowrap text-sm font-bold tracking-tight text-slate-100">
          <Radio className="h-4 w-4 text-cyan-400" />
          Aether
        </span>
        {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
          const isActive = pathname === href || pathname?.startsWith(`${href}/`);
          return (
            <Link
              key={href}
              href={href}
              className={`flex items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-1.5 text-xs font-medium uppercase tracking-widest transition-colors ${
                isActive
                  ? "bg-cyan-500/10 text-cyan-300 border border-cyan-500/40"
                  : "text-slate-400 hover:text-slate-200 border border-transparent"
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
