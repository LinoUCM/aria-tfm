"use client";

import { usePathname } from "next/navigation";
import Navigation from "@/components/Navigation";

export default function LayoutContent({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const isLoginPage = pathname === "/login";

  return (
    <>
      <Navigation />
      <main className={`${isLoginPage ? "" : "ml-64"} min-h-screen`}>
        {children}
      </main>
    </>
  );
}