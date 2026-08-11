import { useEffect, useRef, type PropsWithChildren } from "react";
import { useLocation } from "react-router-dom";

interface PageProps extends PropsWithChildren {
  title: string;
  description: string;
}

export function Page({ title, description, children }: PageProps): React.JSX.Element {
  const location = useLocation();
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    heading.current?.focus();
    document.title = title === "HealthCurve.ai" ? title : `${title} · HealthCurve.ai`;
  }, [location.key, title]);

  return (
    <>
      <h1 ref={heading} tabIndex={-1}>{title}</h1>
      <p className="page-description">{description}</p>
      {children}
    </>
  );
}
