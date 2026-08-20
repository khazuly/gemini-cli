import sys
from .client import parse_cookie_string, save_cookies
from .ui import console

def main_cli():
    if len(sys.argv) > 2 and sys.argv[1] == "--set-cookies":
        cookie_str = sys.argv[2]
        cookies = parse_cookie_string(cookie_str)
        if cookies:
            save_cookies(cookies)
            console.print(f"[green]Saved {len(cookies)} cookies as default session to ~/.gemini-cli/cookies.json[/green]")
        else:
            console.print("[red]Invalid cookie format![/red]")
        return

    from .main import main
    main()


if __name__ == "__main__":
    try:
        main_cli()
    except KeyboardInterrupt:
        console.print("\n[bold red]Bye![/bold red]")
