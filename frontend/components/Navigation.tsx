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

const navItems = [
  { href: "/chat", label: "Chat", icon: MessageSquare, description: "Talk to ARIA" },
  { href: "/admin", label: "Knowledge Base", icon: Database, description: "Manage documents" },
  { href: "/incidents", label: "Incidents", icon: AlertTriangle, description: "Active incidents" },
  { href: "/simulator", label: "Simulator", icon: Zap, description: "Test Datadog alerts" },
  { href: "/audit-logs", label: "Audit Trail", icon: ClipboardList, description: "Remediation history" },
];

export default function Navigation() {
  const pathname = usePathname();
  const router = useRouter();

  // Ocultar el Sidebar en la página de login
  if (pathname === "/login") {
    return null;
  }

  const handleLogout = () => {
    // Eliminar cookies y tokens de almacenamiento
    document.cookie = "aria_token=; path=/; expires=Thu, 01 Jan 1970 00:00:01 GMT;";
    localStorage.removeItem("aria_token");
    
    // Redirigir a login
    router.push("/login");
    router.refresh();
  };

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
        {navItems.map((item) => {
          const isActive = pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={clsx(
                "flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all group",
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
          <span>Cerrar sesión</span>
        </button>
      </div>
    </aside>
  );
}