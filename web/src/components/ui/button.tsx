import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"

const buttonVariants = cva(
    "inline-flex items-center justify-center whitespace-nowrap rounded-(--radius) text-[0.7647rem] font-medium transition-all cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-45 active:scale-[0.97]",
    {
        variants: {
            variant: {
                default: "border border-black/15 bg-background text-foreground hover:bg-secondary hover:border-black/20",
                primary: "bg-accent text-white font-semibold hover:bg-accent/90 dark:bg-accent/10 dark:text-accent dark:border dark:border-black/20 dark:hover:bg-secondary dark:hover:text-foreground",
                destructive: "bg-destructive text-white hover:bg-destructive/90",
                outline: "border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground",
                secondary: "bg-secondary text-secondary-foreground shadow-sm hover:bg-secondary/80",
                ghost: "border border-transparent hover:bg-secondary hover:text-foreground text-muted-foreground",
                ghostDanger: "border border-transparent text-destructive hover:bg-destructive/10 hover:border-destructive text-[0.6471rem]",
                approve: "bg-accent text-white font-semibold hover:bg-accent/90 dark:bg-accent dark:text-white",
                reject: "border border-border bg-secondary text-foreground font-semibold hover:bg-destructive/10 hover:border-destructive/30 hover:text-destructive",
                link: "text-primary underline-offset-4 hover:underline",
            },
            size: {
                default: "h-8 px-3.5 py-1.5",
                sm: "h-6 px-2 text-[0.5882rem]",
                lg: "h-10 rounded-md px-8",
                icon: "h-8 w-8 rounded-full border-none shadow-none shrink-0 flex items-center justify-center auto-cols-auto p-0 hover:bg-secondary hover:text-foreground",
            },
        },
        defaultVariants: {
            variant: "default",
            size: "default",
        },
    }
)

export interface ButtonProps
    extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
    asChild?: boolean
    loading?: boolean
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
    ({ className, variant, size, asChild = false, loading, children, disabled, ...props }, ref) => {
        const Comp = asChild ? Slot : "button"
        return (
            <Comp
                className={cn(buttonVariants({ variant, size, className }))}
                ref={ref}
                disabled={disabled || loading}
                {...props}
            >
                {loading ? (
                    <span className="flex items-center gap-1.5">
                        <svg className="animate-spin h-3 w-3 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                            <path d="M21 12a9 9 0 1 1-6.219-8.56" />
                        </svg>
                        {children}
                    </span>
                ) : children}
            </Comp>
        )
    }
)
Button.displayName = "Button"

export { Button, buttonVariants }
