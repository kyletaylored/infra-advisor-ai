using Azure.AI.OpenAI;
using InfraAdvisor.AgentApi.Models;

namespace InfraAdvisor.AgentApi.Services;

public class SuggestionPoolMaintenanceService : BackgroundService
{
    private readonly SuggestionService _suggestionService;
    private readonly AppState _appState;
    private readonly ILogger<SuggestionPoolMaintenanceService> _logger;
    private readonly AgentService _agentService;
    private const int RefillIntervalSeconds = 1800; // 30 minutes
    private const int PoolMin = 20;

    public SuggestionPoolMaintenanceService(
        SuggestionService suggestionService,
        AppState appState,
        AgentService agentService,
        ILogger<SuggestionPoolMaintenanceService> logger)
    {
        _suggestionService = suggestionService;
        _appState = appState;
        _agentService = agentService;
        _logger = logger;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        // Seed pool on startup if nearly empty
        var initialSize = await _suggestionService.GetPoolSizeAsync();
        if (initialSize < 4 && _appState.LlmConnected)
        {
            _logger.LogInformation("Suggestion pool is small ({Size}), filling on startup", initialSize);
            var chatClient = _agentService.AzureClient.GetChatClient(_agentService.DefaultDeployment);
            await _suggestionService.FillPoolAsync(chatClient);
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
                    var chatClient = _agentService.AzureClient.GetChatClient(_agentService.DefaultDeployment);
                    await _suggestionService.FillPoolAsync(chatClient);
                }
            }
            catch (Exception ex)
            {
                _logger.LogWarning("Suggestion pool maintenance iteration failed: {Error}", ex.Message);
            }
        }
    }
}
