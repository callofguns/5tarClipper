"""Entry point for the Windows .exe (see packaging/gta6clipper.spec)."""
import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from clipper.app import main
    main()
