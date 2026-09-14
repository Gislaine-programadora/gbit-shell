'''
GBit Shell - Entry point
Usage:
    gbit                    start the interactive shell
    gbit -c "<command>"     run a single command and exit
    gbit --version
    gbit --no-banner
'''
import os
import sys

from . import __version__


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if "--version" in argv or "-V" in argv:
        print(f"GBit Shell {__version__}")
        return 0

    if "--help" in argv or "-h" in argv:
        print(
            "GBit Shell — terminal moderno para desenvolvimento\n\n"
            "Uso:\n"
            "  gbit                  inicia o shell interativo\n"
            "  gbit -c \"comando\"     executa um comando e sai\n"
            "  gbit vscode           instala a extensao do VS Code\n"
            "  gbit vscode --build  gera o .vsix sem instalar\n"
            "  gbit --no-banner      inicia sem o logo\n"
            "  gbit --version        mostra a versao\n\n"
            "Dentro do shell, digite 'help' para a lista de comandos."
        )
        return 0

    # Convenience entry point for installers and scripts.
    if argv and argv[0] == "vscode":
        try:
            from .shell import GBitShell
            shell = GBitShell(no_banner=True, interactive=False)
            command = "vscode " + " ".join(argv[1:]).strip()
            return shell.executor.run(command.strip())
        except ImportError as exc:
            print(f"Falta uma dependencia: {exc}", file=sys.stderr)
            return 1

    no_banner = "--no-banner" in argv
    if no_banner:
        argv.remove("--no-banner")

    single_command = None
    if argv and argv[0] in ("-c", "--command"):
        if len(argv) < 2:
            print("erro: -c exige um comando", file=sys.stderr)
            return 2
        single_command = " ".join(argv[1:])

    # Import late so --version stays fast
    try:
        from .shell import GBitShell
    except ImportError as exc:
        missing = str(exc).split("'")
        name = missing[1] if len(missing) > 1 else "dependencias"
        print(
            f"Falta uma dependencia: {name}\n"
            "Instale com:  pip install prompt_toolkit rich psutil",
            file=sys.stderr,
        )
        return 1

    shell = GBitShell(no_banner=no_banner,
                      interactive=(single_command is None))

    if single_command is not None:
        return shell.run_single(single_command)

    try:
        return shell.run()
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
