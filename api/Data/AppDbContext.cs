using Microsoft.EntityFrameworkCore;

namespace BumEngine.Api.Data;

public class AppDbContext : DbContext
{
    public AppDbContext(DbContextOptions<AppDbContext> options) : base(options) { }

    public DbSet<Project> Projects => Set<Project>();
    public DbSet<Variant> Variants => Set<Variant>();

    protected override void OnModelCreating(ModelBuilder b)
    {
        b.Entity<Project>().HasKey(p => p.Id);
        b.Entity<Variant>().HasKey(v => v.Id);
        b.Entity<Variant>()
            .HasOne(v => v.Project)
            .WithMany(p => p.Variants)
            .HasForeignKey(v => v.ProjectId)
            .OnDelete(DeleteBehavior.Cascade);
        b.Entity<Variant>().HasIndex(v => v.ProjectId);
    }
}
