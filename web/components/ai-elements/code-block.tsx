"use client";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { CheckIcon, CopyIcon } from "lucide-react";
import type { ComponentProps, HTMLAttributes } from "react";
import { createContext, memo, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

type CodeBlockProps = HTMLAttributes<HTMLDivElement> & {
  code: string;
  language: string;
  showLineNumbers?: boolean;
};

const CodeBlockContext = createContext({ code: "" });

export const CodeBlockContainer = ({ className, language, style, ...props }: HTMLAttributes<HTMLDivElement> & { language: string }) => (
  <div
    className={cn("group relative w-full overflow-hidden rounded-md border bg-background text-foreground", className)}
    data-language={language}
    style={{ containIntrinsicSize: "auto 200px", contentVisibility: "auto", ...style }}
    {...props}
  />
);

export const CodeBlockHeader = ({ className, ...props }: HTMLAttributes<HTMLDivElement>) => (
  <div className={cn("flex items-center justify-between border-b bg-muted/80 px-3 py-2 text-muted-foreground text-xs", className)} {...props} />
);

export const CodeBlockTitle = ({ className, ...props }: HTMLAttributes<HTMLDivElement>) => (
  <div className={cn("flex items-center gap-2", className)} {...props} />
);

export const CodeBlockFilename = ({ className, ...props }: HTMLAttributes<HTMLSpanElement>) => (
  <span className={cn("font-mono", className)} {...props} />
);

export const CodeBlockActions = ({ className, ...props }: HTMLAttributes<HTMLDivElement>) => (
  <div className={cn("-my-1 -mr-1 flex items-center gap-2", className)} {...props} />
);

export const CodeBlockContent = memo(function CodeBlockContent({ code, showLineNumbers = false }: { code: string; language: string; showLineNumbers?: boolean }) {
  const lines = useMemo(() => code.split("\n"), [code]);
  return (
    <div className="relative max-h-96 overflow-auto">
      <pre className="m-0 p-4 text-sm"><code className="font-mono text-sm">{showLineNumbers
        ? lines.map((line, index) => <span className="block" key={`${index}-${line.slice(0, 24)}`}><span className="mr-4 inline-block w-8 select-none text-right text-muted-foreground/50">{index + 1}</span>{line || "\n"}</span>)
        : code}</code></pre>
    </div>
  );
});

export const CodeBlock = memo(function CodeBlock({ code, language, showLineNumbers = false, className, children, ...props }: CodeBlockProps) {
  const contextValue = useMemo(() => ({ code }), [code]);
  return (
    <CodeBlockContext.Provider value={contextValue}>
      <CodeBlockContainer className={className} language={language} {...props}>
        {children}
        <CodeBlockContent code={code} language={language} showLineNumbers={showLineNumbers} />
      </CodeBlockContainer>
    </CodeBlockContext.Provider>
  );
});

export type CodeBlockCopyButtonProps = ComponentProps<typeof Button> & {
  onCopy?: () => void;
  onError?: (error: Error) => void;
  timeout?: number;
};

export const CodeBlockCopyButton = ({ onCopy, onError, timeout = 2000, children, className, ...props }: CodeBlockCopyButtonProps) => {
  const [isCopied, setIsCopied] = useState(false);
  const timeoutRef = useRef<number>(0);
  const { code } = useContext(CodeBlockContext);
  const copyToClipboard = useCallback(async () => {
    try {
      if (!navigator?.clipboard?.writeText) throw new Error("Clipboard API not available");
      await navigator.clipboard.writeText(code);
      setIsCopied(true);
      onCopy?.();
      timeoutRef.current = window.setTimeout(() => setIsCopied(false), timeout);
    } catch (error) {
      onError?.(error as Error);
    }
  }, [code, onCopy, onError, timeout]);
  useEffect(() => () => window.clearTimeout(timeoutRef.current), []);
  const Icon = isCopied ? CheckIcon : CopyIcon;
  return <Button className={cn("shrink-0", className)} onClick={copyToClipboard} size="icon" variant="ghost" {...props}>{children ?? <Icon size={14} />}</Button>;
};
