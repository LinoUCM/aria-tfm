"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  MessageSquare,
  Database,
  AlertTriangle,
  Zap,
  Activity,
  Shield,
  LogOut,
  ClipboardList,
} from "lucide-react";
import { clsx } from "clsx";
import { useState, useEffect } from "react";
import { getCurrentUser, logoutUser } from "@/lib/auth";

const navItems: {
  href: string; label: string; icon: any; description: string; roles: string[] | null;
}[] = [
  { href: "/chat", label: "Chat", icon: MessageSquare, description: "Talk to ARIA", roles: null },
  { href: "/admin", label: "Knowledge Base", icon: Database, description: "Manage documents", roles: ["ADMIN", "ON_CALL"] },
  { href: "/incidents", label: "Incidents", icon: AlertTriangle, description: "Active incidents", roles: null },
  { href: "/simulator", label: "Simulator", icon: Zap, description: "Test Datadog alerts", roles: ["ADMIN", "ON_CALL"] },
  { href: "/audit-logs", label: "Audit Trail", icon: ClipboardList, description: "Remediation history", roles: null },
  { href: "/admin/users", label: "Users", icon: Shield, description: "Manage team access", roles: ["ADMIN"] },
];

export default function Navigation() {
  const pathname = usePathname();
  const router = useRouter();

  const [currentUser, setCurrentUser] = useState<any>(null);

  useEffect(() => {
    setCurrentUser(getCurrentUser());
  }, [pathname]);

  // Ocultar el Sidebar en la página de login
  if (pathname === "/login") {
    return null;
  }

  const handleLogout = () => {
    logoutUser();
  };

  const visibleNavItems = navItems.filter(
    (item) => !item.roles || (currentUser?.role && item.roles.includes(currentUser.role.toUpperCase()))
  );

  // Evita que un item padre (p.ej. /admin) se marque activo a la vez que un
  // item hijo más específico (p.ej. /admin/users) cuando ambos son prefijo
  // de la ruta actual — nos quedamos solo con el prefijo más largo.
  const activeHref = visibleNavItems.reduce<string | null>((best, item) => {
    if (pathname.startsWith(item.href) && (!best || item.href.length > best.length)) {
      return item.href;
    }
    return best;
  }, null);

  return (
    <aside className="fixed left-0 top-0 h-full w-64 bg-gray-900 border-r border-gray-800 flex flex-col z-50">
      {/* Logo */}
      <div className="p-6 border-b border-gray-800">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 bg-blue-600 rounded-lg flex items-center justify-center">
            <Shield className="w-5 h-5 text-white" />
          </div>
          <div>
            <h1 className="text-white font-bold text-lg leading-none">ARIA</h1>
            <p className="text-gray-400 text-xs mt-0.5">Operations Intelligence</p>
          </div>
        </div>
      </div>

      {/* Nav Items */}
      <nav className="flex-1 p-4 space-y-1">
        {visibleNavItems.map((item) => {
          const isActive = item.href === activeHref;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={clsx(
                "flex items-center gap-3 px-3 py-2.5 rounded-lg group",
                isActive
                  ? "bg-blue-600/20 text-blue-400 border border-blue-500/30"
                  : "text-gray-400 hover:text-gray-100 hover:bg-gray-800"
              )}
            >
              <item.icon
                className={clsx(
                  "w-5 h-5 flex-shrink-0",
                  isActive ? "text-blue-400" : "text-gray-500 group-hover:text-gray-300"
                )}
              />
              <div>
                <div className="text-sm font-medium">{item.label}</div>
                <div className="text-xs text-gray-500">{item.description}</div>
              </div>
            </Link>
          );
        })}
      </nav>

      {/* Status & Logout */}
      <div className="p-4 border-t border-gray-800 space-y-3">
        {currentUser && (
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-gray-800/50">
            <div className="w-7 h-7 rounded-full bg-blue-600/20 border border-blue-500/30 flex items-center justify-center text-blue-400 text-xs font-semibold flex-shrink-0">
              {(currentUser.full_name || currentUser.username || "?").charAt(0).toUpperCase()}
            </div>
            <div className="min-w-0">
              <div className="text-xs font-medium text-gray-200 truncate">
                {currentUser.full_name || currentUser.username}
              </div>
              <div className="text-xs text-gray-500 capitalize">{currentUser.role}</div>
            </div>
          </div>
        )}

        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-gray-800/50">
          <Activity className="w-4 h-4 text-green-400" />
          <span className="text-xs text-gray-400">Backend connected</span>
          <div className="ml-auto w-2 h-2 bg-green-400 rounded-full animate-pulse" />
        </div>

        <button
          onClick={handleLogout}
          className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium text-gray-400 hover:text-red-400 hover:bg-red-500/10 transition-all group"
        >
          <LogOut className="w-4 h-4 text-gray-500 group-hover:text-red-400" />
          <span>Log out</span>
        </button>
      </div>
    </aside>
  );
}