import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";

export default function Home() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-background px-4">
      <div className="mx-auto max-w-2xl text-center">
        <h1 className="text-5xl font-bold tracking-tight sm:text-6xl">
          OpenBull
        </h1>
        <p className="mt-4 text-lg text-muted-foreground">
          Options Trading Platform for Indian Brokers
        </p>
        <p className="mt-2 text-sm text-muted-foreground">
          Connect your broker, manage your portfolio, and execute trades from a
          single platform.
        </p>
        <div className="mt-8 flex items-center justify-center gap-4">
          <Link to="/login">
            <Button size="lg">Get Started</Button>
          </Link>
        </div>
      </div>
      <footer className="absolute bottom-6 text-xs text-muted-foreground">
        OpenBull - Open Source Trading Platform
      </footer>
    </div>
  );
}
