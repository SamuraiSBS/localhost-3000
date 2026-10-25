import { cn } from '@/lib/utils';
export function Badge({ children, className, variant, ...props }: any) {
  return <span data-variant={variant} className={cn('inline-flex items-center gap-1 rounded-md border border-white/10 bg-white/5 px-1.5 py-0.5 text-xs font-medium [&>svg]:size-3', className)} {...props}>{children}</span>;
}
