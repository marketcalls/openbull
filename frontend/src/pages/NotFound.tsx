import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";

export default function NotFound() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-background px-4">
      <div className="text-center">
        <h1 className="text-6xl font-bold text-foreground">404</h1>
        <p className="mt-4 text-lg text-muted-foreground">Page not found</p>
        <p className="mt-2 text-sm text-muted-foreground">
          The page you are looking for does not exist.
        </p>
        <div className="mt-8">
          <Link to="/dashboard">
            <Button>Back to Dashboard</Button>
          </Link>
        </div>
      </div>
    </div>
  );
}
