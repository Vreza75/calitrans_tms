"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/lib/auth/AuthContext";

export default function Home() {
  const router = useRouter();
  const { status, isAuthenticated } = useAuth();

  useEffect(() => {
    if (status === "loading") return;
    router.replace(isAuthenticated ? "/app/loads" : "/login");
  }, [status, isAuthenticated, router]);

  return null;
}
