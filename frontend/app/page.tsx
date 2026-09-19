import { Suspense } from "react";
import TowerApp from "@/components/TowerApp";

export default function Page() {
  return (
    <Suspense fallback={<div className="h-screen w-screen bg-bg" />}>
      <TowerApp />
    </Suspense>
  );
}
