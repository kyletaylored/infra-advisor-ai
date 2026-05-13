using InfraAdvisor.AgentApi.Models;

namespace InfraAdvisor.AgentApi.Services;

public class SuggestionPoolMaintenanceService : BackgroundService
{
    private readonly SuggestionService _suggestionService;
    private readonly AppState _appState;
    private readonly ILogger<SuggestionPoolMaintenanceService> _logger;
    private const int RefillIntervalSeconds = 1800; // 30 minutes
    private const int PoolMin = 20;

    public SuggestionPoolMaintenanceService(
        SuggestionService suggestionService,
        AppState appState,
        ILogger<SuggestionPoolMaintenanceService> logger)
    {
        _suggestionService = suggestionService;
        _appState = appState;
        _logger = logger;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        // Seed pool on startup if nearly empty
        var initialSize = await _suggestionService.GetPoolSizeAsync();
        if (initialSize < 4 && _appState.LlmConnected)
        {
            _logger.LogInformation("Suggestion pool is small ({Size}), filling on startup", initialSize);
            await _suggestionService.FillPoolAsync();
        }

        // Background maintenance loop
        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                await Task.Delay(TimeSpan.FromSeconds(RefillIntervalSeconds), stoppingToken);
            }
            catch (OperationCanceledException)
            {
                break;
            }

            try
            {
                var size = await _suggestionService.GetPoolSizeAsync();
                if (size < PoolMin && _appState.LlmConnected)
                {
                    _logger.LogInformation("Suggestion pool at {Size}, triggering refill", size);
                    await _suggestionService.FillPoolAsync();
                }
            }
            catch (Exception ex)
            {
                _logger.LogWarning("Suggestion pool maintenance iteration failed: {Error}", ex.Message);
            }
        }
    }
}
